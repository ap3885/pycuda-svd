"""
Author: Dwiref Oza

Based on the serial implementation by Ananye Pandey
"""

import numpy as np
import random
import matplotlib.pyplot as plt

import pycuda.driver as cuda
import pycuda.autoinit
from pycuda import compiler, gpuarray, tools

import time

"""
###############################################################################
                    define kernel codes and how to call them
###############################################################################
"""



class cuda_Transpose:
    """
    Class of functions pertaining to computing a matrix transpose
    using for loops and parallelized pyCuda code.
    """
    def __init__(self):

        # Kernal code:
        self.transpose_kernel_code = """
        __global__ void parTranspose(float *idata, float *odata, int cols, int rows) {
            int ix = blockIdx.x * blockDim.x + threadIdx.x;
            int iy = blockIdx.y * blockDim.y + threadIdx.y;
            if ((ix < cols) && (iy < rows)) {
                odata[iy*cols + ix] = idata[ix*rows + iy];
            }
        }
        """

    def transpose_parallel(self, a_cpu):
        self.x = a_cpu
        x_gpu = gpuarray.to_gpu(self.x)
        self.y_gpu = gpuarray.empty((self.x.shape[1], self.x.shape[0]), np.float32)

        M = self.x.shape[0]
        N = self.x.shape[1]

        mod = compiler.SourceModule(self.transpose_kernel_code)
        timing = []
        cTranspose = mod.get_function("parTranspose")
        cTranspose(
            x_gpu,
            self.y_gpu,
            np.int32(self.x.shape[0]),
            np.int32(self.x.shape[1]),
            block = (32, 32, 1),
            grid = (np.int(np.ceil(np.float32(M)/np.float32(32))), np.int(np.ceil(np.float32(N)/np.float32(32))), 1)
        )

        return self.y_gpu.get()


class gpuMul:
    def __init__(self):

        self.mul_kernel_code_aa = """
        __global__ void optMul(float * a, int m, int n, float * b, float * c,) {

            #define TILE_WIDTH 16
            int Row = blockIdx.y * blockDim.y + threadIdx.y;
            int Col = blockIdx.x * blockDim.x + threadIdx.x;

            __shared__ float shared_A[TILE_WIDTH][TILE_WIDTH];
            __shared__ float shared_B[TILE_WIDTH][TILE_WIDTH];

            float Cval = 0;
            int tileid;
            int i;

            for (tileid = 0; tileid < (n-1) / TILE_WIDTH + 1; tileid++) {
                if(Row<m && tileid*TILE_WIDTH+ threadIdx.x<n)
                    shared_A[threadIdx.y][threadIdx.x] = a[Row * n + tileid * TILE_WIDTH + threadIdx.x];
                else
                    shared_A[threadIdx.y][threadIdx.x] = 0;
                if(Col<m && tileid*TILE_WIDTH+threadIdx.y < n)
                    shared_B[threadIdx.y][threadIdx.x] = b[(tileid * TILE_WIDTH + threadIdx.y) * m + Col];
                else
                    shared_B[threadIdx.y][threadIdx.x] =0;


                __syncthreads();
                for (i=0; i < TILE_WIDTH; i++)
                    Cval += shared_A[threadIdx.y][i] * shared_B[i][threadIdx.x];

                __syncthreads();
                }

                if(Row< m && Col<m)
                    c[Row * m + Col] = Cval;
            }

        """

        self.mul_kernel_code_2 = """
            __global__ void kernel_MatMul(double* A, int ARows, int ACols, double* B, int BRows, int BCols, double* C) {
                # define TILE_DIM 16
                __shared__ double As[TILE_DIM][TILE_DIM];
                __shared__ double Bs[TILE_DIM][TILE_DIM];
                double CValue = 0;
                int Row = blockIdx.y*TILE_DIM + threadIdx.y;
                int Col = blockIdx.x*TILE_DIM + threadIdx.x;
                int CRows = ARows;
                int CCols = BCols;
                for (int k = 0; k < (TILE_DIM + ACols - 1)/TILE_DIM; k++) {

                    if (k*TILE_DIM + threadIdx.x < ACols && Row < ARows) As[threadIdx.y][threadIdx.x] = A[Row*ACols + k*TILE_DIM + threadIdx.x];
                    else As[threadIdx.y][threadIdx.x] = 0.0;

                    if (k*TILE_DIM + threadIdx.y < BRows && Col < BCols)  Bs[threadIdx.y][threadIdx.x] = B[(k*TILE_DIM + threadIdx.y)*BCols + Col];
                    else Bs[threadIdx.y][threadIdx.x] = 0.0;

                    __syncthreads();

                    for (int n = 0; n < TILE_DIM; ++n) CValue += As[threadIdx.y][n] * Bs[n][threadIdx.x];

                    __syncthreads();

                }
                if (Row < CRows && Col < CCols) C[((blockIdx.y * blockDim.y + threadIdx.y)*CCols)+(blockIdx.x*blockDim.x)+threadIdx.x]=CValue;

            }
        """

        self.mul_kernel_code = """
            #define BLOCK_SIZE 16
            __global__ void kernel_MatMul(float *A, int rA, int cA, float *B, int rB, int cB, float *C) {
                int bIDx = blockIdx.x, bIDy = blockIdx.y, tIDx = threadIdx.x, tIDy = threadIdx.y;
                int row_ = bIDy * BLOCK_SIZE + tIDy;
                int col_ = bIDx * BLOCK_SIZE + tIDx;
                __shared__ float A_sub[BLOCK_SIZE][BLOCK_SIZE];
                __shared__ float B_sub[BLOCK_SIZE][BLOCK_SIZE];
                float C_sub = 0.0;
                for (int m = 0; m < (BLOCK_SIZE + cA - 1) / BLOCK_SIZE; m++) {
                    if (m * BLOCK_SIZE + tIDx < cA && row_ < rA) {
                        A_sub[tIDy][tIDx] = A[row_ * cA + m * BLOCK_SIZE + tIDx];
                    }
                    else {
                        A_sub[tIDy][tIDx] = 0.0;
                    }
                    if (m * BLOCK_SIZE + tIDy < rB && col_ < cB) {
                        B_sub[tIDy][tIDx] = B[(m * BLOCK_SIZE + tIDy) * cB + col_];
                    }
                    else {
                        B_sub[tIDy][tIDx] = 0.0;
                    }
                    __syncthreads();
            #pragma unroll
                    for (int k = 0; k < BLOCK_SIZE; k++) {
                        C_sub += A_sub[tIDy][k] * B_sub[k][tIDx];
                    }
                    __syncthreads();
                }
                if (row_ < rA && col_ < cB) {
                    C[cB * BLOCK_SIZE * bIDy + BLOCK_SIZE * bIDx + cB * tIDy + tIDx] = C_sub;
                }
            }
        """

    def MatMul(self, A, rA, cA, B, rB, cB):

            self.C_gpu = gpuarray.empty((A.shape[0], B.shape[1]), dtype = np.float32)
            self.A_gpu = gpuarray.to_gpu(A)
            self.B_gpu = gpuarray.to_gpu(B)

            mod = compiler.SourceModule(self.mul_kernel_code)
            dev_mul = mod.get_function("kernel_MatMul")

            grid_x = np.int(np.ceil(cB*1.0/16))
            grid_y = np.int(np.ceil(rA*1.0/16))

            dev_mul(
                self.A_gpu, rA, cA,
                self.B_gpu, rB, cA,
                self.C_gpu,
                block = (16, 16, 1),
                grid = (grid_x, grid_y, 1)
            )

            """
            dev_mul(
                self.A_gpu, rA, cA,
                self.B_gpu,
                self.C_gpu,
                block = (16, 16, 1),
                grid = (grid_x, grid_y, 1)
            )
            """
            return self.C_gpu.get()

# computeParams.compute_params
class computeParams:
    def __init__(self):

        self.compute_params_kernel_code = """

            __global__ void kernel_compute_params(float *device_A, int P, int iter, float *device_sine, float *device_cosine, int *device_IterBlockToElem) {
                /*1 Block, P/2 threads: threadID t handles params for its alloted pair (for a particular device_iter)*/
                # define EPSILON 1e-4
                int localID = threadIdx.x;
                int k, l;
                float elem, y, d, r, c, s; //,t
                k = device_IterBlockToElem[iter*P+localID*2]; //row
                l = device_IterBlockToElem[iter*P+localID*2+1]; //col
                elem = device_A[k * P + l];
                __syncthreads();
                y = (device_A[l * P + l] - device_A[k * P + k]) * 0.5;
                __syncthreads();
                d = fabs(y) + sqrt(elem * elem + y * y);
                r = sqrt(elem * elem + d * d);
                if (r < EPSILON) {
                    c = 1.0;
                    s = 0.0;
                }
                else {
                    c = d / r;
                    s = y / fabs(y) * elem / r; //t=y/fabs(y)*p*p/d;
                }
                __syncthreads();
                if (k<P && l<P){
                device_cosine[k * P + l] = c;
                device_sine[k * P + l] = s;
                }
            }
        """

    def compute_params(self, A, P, itr, iterblock):
        self.A_gpu = gpuarray.to_gpu(A)
        self.iterBlock_device = gpuarray.to_gpu(iterblock)
        self.dev_sin = gpuarray.empty((P, P), np.float32)
        self.dev_cos = gpuarray.empty((P, P), np.float32)
        # self.iterBlock_device = gpuarray.empty((P-1)*P / 2 * 2), astype.int)
        if (P % 2 == 0):
            grid_size = np.int(P / 2)
        else:
            grid_size = np.int(P / 2 + 1)
        mod = compiler.SourceModule(self.compute_params_kernel_code)
        compute_params_code = mod.get_function("kernel_compute_params")
        compute_params_code(
            self.A_gpu, P, itr,
            self.dev_sin,
            self.dev_cos,
            self.iterBlock_device,
            block = (grid_size, grid_size, 1))
        # block size?
        dc = self.dev_cos.get()
        ds = self.dev_sin.get()
        self.A_gpu.get()
        self.iterBlock_device.get()

        return ds, dc

class dimUpdate:

    def __init__(self,P):

        self.row_update_kernel_code = """
            __global__ void kernel_row_update(int iter, float *device_A, float *device_X, int P, float *device_sine, float *device_cosine, int *device_IterBlockToElem) {
                int localID = threadIdx.x;
                int blockID = blockIdx.x;
                /*Based on blockID [total blocks=P/2], compute the corresponding two rows: p,q for device_iter*/
                __shared__ int row_pair[2];
                __shared__ float params[2]; //[sin_, cos_]
                if (localID == 0)            //to minimize global memory access latency at the cost of divergence
                {
                    row_pair[0] = device_IterBlockToElem[iter*P+blockID * 2];
                    row_pair[1] = device_IterBlockToElem[iter*P+blockID * 2 + 1];
                    params[0] = device_sine[row_pair[0] * P + row_pair[1]];
                    params[1] = device_cosine[row_pair[0] * P + row_pair[1]];
                }
                __syncthreads(); //all "P" threads in the block are synchronized and have access to row_pair(k,l) and params
                //CHECKPOINT: Can you reduce shared-memory bank conflicts here?
                int k = row_pair[0], l = row_pair[1];
                float sin_ = params[0], cos_ = params[1], elem_k=device_A[k*P+localID], elem_l=device_A[l * P + localID];
                /*Concurrent modifications to all row pairs(k,l) [different blocks]*/
                /*Concurrent modifications to different-column elements of a row pair: ["P" threads of the block]*/
                /*X is col-major, i.e. write in X-transpose*/
                device_X[localID * P + k] = elem_k * cos_ - elem_l * sin_;
                device_X[localID * P + l] = elem_k * sin_ + elem_l * cos_;
            }
        """

        self.col_update_kernel_code = """
            __global__ void kernel_col_update(int iter, float *device_A, float *device_X, int P, float *device_eigenvectors, float *device_sine, float *device_cosine, int *device_IterBlockToElem) {
                int localID = threadIdx.x;
                int blockID = blockIdx.x;
                /*Based on blockID [total blocks=P/2], compute the corresponding two cols: p,q for device_iter*/
                __shared__ int col_pair[2];
                __shared__ float params[2]; //[sin_, cos_]
                if (localID == 0)            //to minimize global memory access latency at the cost of divergence
                {
                    col_pair[0] = device_IterBlockToElem[iter*P+blockID * 2];
                    col_pair[1] = device_IterBlockToElem[iter*P+blockID * 2 + 1];
                    params[0] = device_sine[col_pair[0] * P + col_pair[1]];
                    params[1] = device_cosine[col_pair[0] * P + col_pair[1]];
                }
                __syncthreads(); //all "P" threads in the block are synchronized and have access to row_pair(k,l) and params
                int k = col_pair[0], l = col_pair[1];
                float sin_ = params[0], cos_ = params[1];
                /*Concurrent modifications to all row pairs(k,l) [different blocks]*/
                /*Concurrent modifications to different-column elements of a row pair: ["P" threads of the block]*/
                float new_eigen_k, new_eigen_l;
                int kp = k*P + localID, lp = l *P+localID;
                device_A[kp] = device_X[kp] * cos_ - device_X[lp] * sin_;
                __syncthreads();
                device_A[lp] = device_X[kp] * sin_ + device_X[lp] * cos_;
                __syncthreads();
                new_eigen_k = device_eigenvectors[kp]*cos_ - device_eigenvectors[lp]*sin_;
                __syncthreads();
                new_eigen_l = device_eigenvectors[kp]*sin_ + device_eigenvectors[lp]*cos_;
                __syncthreads();
                device_eigenvectors[kp] = new_eigen_k;
                device_eigenvectors[lp] = new_eigen_l;
                __syncthreads();
            }
        """

        E = np.diag(np.ones((P), dtype = np.float32))
        self.device_eigenvectors = gpuarray.to_gpu(E)

    def row_update(self, itr, A, X_device, P, sin, cos, iterBlock):
        self.A_device = gpuarray.to_gpu(A)
        self.X_device = gpuarray.to_gpu(X_device)
        self.dev_sin = gpuarray.to_gpu(sin)
        self.dev_cos = gpuarray.to_gpu(cos)
        self.iterBlock_device = gpuarray.to_gpu(iterBlock)

        mod1 = compiler.SourceModule(self.row_update_kernel_code)
        row_update_code = mod1.get_function("kernel_row_update")
        if (P % 2 == 0):
            grid_size = np.int(P / 2)
        else:
            grid_size = np.int(P / 2 + 1)

        row_update_code(
            itr, self.A_device,
            self.X_device, P,
            self.dev_sin, self.dev_cos,
            self.iterBlock_device,
            block = (np.int(P), np.int(P), 1),
            grid = (np.int(grid_size), np.int(grid_size),1)
        )
        return self.X_device.get()

    def col_update(self, itr, A, X_device, P, sin, cos, iterBlock):
        self.A_device = gpuarray.to_gpu(A)
        self.X_device = gpuarray.to_gpu(X_device)
        self.dev_sin = gpuarray.to_gpu(sin)
        self.dev_cos = gpuarray.to_gpu(cos)
        self.iterBlock_device = gpuarray.to_gpu(iterBlock)

        if (P % 2 == 0):
            grid_size = np.int(P / 2)
        else:
            grid_size = np.int(P / 2 + 1)

        mod2 = compiler.SourceModule(self.col_update_kernel_code)
        col_update_code = mod2.get_function("kernel_col_update")

        col_update_code(
            itr, self.A_device,
            self.X_device, P,
            self.device_eigenvectors,
            self.dev_sin, self.dev_cos,
            self.iterBlock_device,
            block = (np.int(P), np.int(P), 1),
            grid = (np.int(grid_size), np.int(grid_size),1)
        )

        return self.device_eigenvectors.get()

"""
###############################################################################
                                 On to PCA and SVD
###############################################################################
"""


def cudaSVD(N, P, D):

    # Perform SVD for D_T
    # Get eigen values and eigen vectors for D_T*D

    chess_params_kernel_code = """
      __device__ void chess_tourney_params(int P, int *row_pair, int iter) {
            //NOTE: here, row_pair is thread-local
            int localID = threadIdx.x;
            int index1, index2;
            index1 = (localID + iter) % (P - 1);
            if (localID != 0) {
                index2 = (P - localID + iter - 1) % (P - 1);
            }
            else {
                index2 = P - 1;
            }
            row_pair[0] = min(index1, index2);
            row_pair[1] = max(index1, index2);
        }
    __global__ void kernel_compute_all_chess_params(int P, int *device_IterBlockToElem) {
        int blockID = blockIdx.x;
        //each ONE of the P-1 blocks is responsible for computing chess-tourney parameters for ONE of the P-1 iterations
        int index = blockID*P + threadIdx.x*2;
        int *row_pair = (int *) malloc(sizeof(int)*2);
        chess_tourney_params(P, row_pair, blockID);
        device_IterBlockToElem[index] = row_pair[0]; //|=(P-1)X(P/2*2)
        device_IterBlockToElem[index+1] = row_pair[1];
        free(row_pair);
    }
    """
    ###########################################################################
    # STREAM PARALLELIZATION
    t = cuda_Transpose()
    g = gpuMul()

    iterBlock_device = gpuarray.empty(((P-1), np.int(np.ceil(P/2)), 2), np.int32)
    mod = compiler.SourceModule(chess_params_kernel_code)
    dev_chess = mod.get_function("kernel_compute_all_chess_params")

    dev_chess(np.int32(P), iterBlock_device, block = (np.int(P-1), np.int(np.ceil(P/2)), 1),
              grid = (np.int(P-1), np.int(P-1),1))
    iterBlock = iterBlock_device.get()
    # cudaAsynccopy something
    D_T = t.transpose_parallel(D)
    ###########################################################################
    A = g.MatMul(D_T, np.int32(P), np.int32(N), D, np.int32(N), np.int32(P))
    eigenvectors = np.ones((P, P), np.float32)
    counter = 0

    MAX_SWEEPS = 30
    EPSILON = 1e-4
    THRESHOLD = 1e-4
    MAX_BLOCK_SIZE = 1024
    MAX_SWEEPS = 30
    MAX_ITER = 10000000
    MULTIPLY_BLOCK_SIZE = 16

    itr = 0
    cP = computeParams()
    dU = dimUpdate(P)
    X = np.zeros((P,P), dtype = np.float32)
    while(counter < 30):
        while(itr < P-1):
            # Compute rotation parameters: sine and cosine
            # for all (p, q), q>p
            sin, cos = cP.compute_params(A, np.int32(P), np.int32(itr), iterBlock)
            # row update
            X = dU.row_update(np.int32(itr), np.float32(A), np.float32(X),
                            np.int32(P), np.float32(sin), np.float32(cos), iterBlock)
        # col update
            eigenvectors = dU.col_update(np.int32(itr), np.float32(A), np.float32(X),
                                        np.int32(P), np.float32(sin), np.float32(cos), iterBlock)
            itr = itr + 1

        counter = counter + 1
    eigenvectors_T = t.transpose_parallel(eigenvectors)

    eigenvalues = np.ones(P)
    e_indices = np.ones(P)

    for i in range(P):
        eigenvalues[i] = A[i][i]
        e_indices[i] = i

     # sort eigenvalues in descending order along with corresponding indices
    eigenvalues = np.sort(eigenvalues)
    new_indices = np.argsort(e_indices)
    eigenvalues = np.flip(eigenvalues)
    new_indices = np.flip(new_indices)

    # compute sigma
    SIGMA = np.ones(P, np.float32)
    sum_variance = 0.0
    sum_variance = np.sum(eigenvalues)
    SIGMA = np.sqrt(eigenvalues)
    U = np.empty((P,P), dtype = np.float32)
    # compute U
    for i in range(P):
        for j in range(P):
            U[i][j] = eigenvectors[i][new_indices[j]]

    # calculate V_T

    inv_SIGMA = np.ones((N, P), np.float32)
    for i in range(P):
        inv_SIGMA[i][i] = 1.0 / SIGMA[i]

    U_T = t.transpose_parallel(U)
    prod = g.MatMul(inv_SIGMA, np.int32(N), np.int32(P), U_T, np.int32(P), np.int32(P))
    # V_T = inv_SIGMA * U_T * D_T
    V_T = g.MatMul(prod, np.int32(N), np.int32(P), D_T, np.int32(P), np.int32(N))
    print(U)

    return SIGMA, U, V_T


if __name__ =='__main__':

    random.seed(1)
    A = np.random.randint(0,9,(10, 10)).astype(np.float32)
    A1 = np.dot(A.T,A)

    #serial jacobi method for SVD
    s, u, vt = cudaSVD(A.shape[0],A.shape[1],A)


    #numpy verification
    s1,v1 = np.linalg.eig(A1)

    #print results

    print("Serial Eigenvalues: \n", s)
    print("Numpy Eigenvalues: \n",np.sqrt(s1))
    print("Serial Eigenvectors: \n", u)
    print("Numpy Eigenvectors: \n", v1)
