# MIT License

# Copyright (c) 2026 SimBricks

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# Where "make qemu-install" places the QEMU binaries. Inside a conda build this
# is the build prefix; for a local dev build override it, e.g. PREFIX=$(pwd)/out.
PREFIX            ?= $(CURDIR)/out

# Location of the SimBricks headers/libraries QEMU's --enable-simbricks build
# links against. Default to PREFIX; the conda recipe points these at the conda
# prefix. For local development set them to wherever simbricks-lib is installed.
SIMBRICKS_INC_DIR ?= $(PREFIX)/include
SIMBRICKS_LIB_DIR ?= $(PREFIX)/lib

# Compilers and python interpreter (overridable by conda / the environment).
CC                ?= cc
CXX               ?= c++
PYTHON            ?= python

# Optional: redirect conda-build output, e.g. OUTPUT_FOLDER=./conda-out.
OUTPUT_FOLDER     ?=
OUTPUT_FLAG       := $(if $(OUTPUT_FOLDER),--output-folder $(OUTPUT_FOLDER))
# Conda channels searched by `conda build`. The SimBricks channel hosts external
# deps not built here (e.g. simbricks-lib, simbricks-orchestration); conda-forge
# provides the rest. Override to point at a different channel if needed.
SIMB_CONDA_CHANNEL:= -c https://conda.simbricks.io/latest
BASE_BUILD_CMD    := conda build $(SIMB_CONDA_CHANNEL) -m conda-recipes/conda_build_config.yaml $(OUTPUT_FLAG)

.PHONY: all qemu-build qemu-install python-develop \
        python-conda qemu-conda conda-packages pypi-build pypi-publish clean

## --- QEMU (C sources in qemu/) --------------------------------------------

# Configure + build QEMU. The stamp file tracks a completed build so repeated
# invocations are cheap. The leading '+' forwards the make jobserver to the
# nested QEMU build for parallelism.
qemu/ready: qemu
	+export CPP="$(CC) -E"; \
	cd qemu && ./configure \
			--target-list=x86_64-softmmu \
			--prefix="$(PREFIX)" \
			--disable-werror \
			--cc="$(CC)" \
			--cxx="$(CXX)" \
			--host-cc="$(CC)" \
			--with-pkgversion=SimBricks \
			--extra-cflags="$(CFLAGS) -I$(SIMBRICKS_INC_DIR)" \
			--extra-ldflags="$(LDFLAGS) --sysroot=$(CONDA_BUILD_SYSROOT) -L$(SIMBRICKS_LIB_DIR) -lstdc++ -latomic" \
			--enable-simbricks \
			--enable-simbricks-pci \
			--enable-simbricks-eth && \
	  $(MAKE)
	touch $@

# Build QEMU (clean named alias for the stamp target).
qemu-build: qemu/ready

# Install the built QEMU binaries into $(PREFIX).
qemu-install: qemu/ready
	cd qemu && $(MAKE) install

## --- Python integration package (simbricks-qemu-python/) -------------------

# Editable install for local development (not used by the conda build).
python-develop:
	$(PYTHON) -m pip install -e ./simbricks-qemu-python

## --- Conda packages --------------------------------------------------------

# Build the noarch python conda package.
python-conda:
	$(BASE_BUILD_CMD) conda-recipes/simbricks-qemu-python

# Build the compiled QEMU conda package. It depends at runtime on the python
# package, so build that first and let conda resolve it from the local channel.
qemu-conda: python-conda
	$(BASE_BUILD_CMD) conda-recipes/simbricks-qemu-bin

# Build both conda packages in dependency order.
conda-packages: python-conda qemu-conda

## --- PyPI packages ---------------------------------------------------------

pypi-build:
	poetry build -C simbricks-qemu-python

pypi-publish: pypi-build
	poetry publish -C simbricks-qemu-python

## --- Default target ----------------------------------------------------------

# Default: local dev build of both halves.
all: conda-packages

## --- Housekeeping ----------------------------------------------------------

clean:
	rm -f qemu/ready
	-cd qemu && $(MAKE) clean
	rm -rf simbricks-qemu-python/dist
