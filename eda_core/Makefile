# Builds the fast_eda pybind11 extension out of tree (default: ../build/eda).
PYTHON   ?= python3
CXX      ?= g++
OUT      ?= ../build/eda
CXXFLAGS ?= -O3 -std=c++17 -fPIC
INCLUDES := $(shell $(PYTHON) -m pybind11 --includes)
SUFFIX   := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
TARGET   := $(OUT)/fast_eda$(SUFFIX)

.PHONY: all clean
all: $(TARGET)

$(TARGET): fast_eda.cpp
	mkdir -p $(OUT)
	$(CXX) $(CXXFLAGS) -shared $(INCLUDES) $< -o $@
	$(PYTHON) -c "import sys; sys.path.insert(0, '$(OUT)'); import fast_eda; fast_eda.FastGraph().cone_of_influence(['x'])"

clean:
	rm -f $(TARGET)
