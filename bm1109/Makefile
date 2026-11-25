# ============================================================================
#  BM1109 — отдельная сборка измерительной прошивки R и L.
#
#  Сборка не пересекается с OEW:
#    - свои объектные файлы и ELF в bm1109/build/;
#    - корневой Makefile и его цель all не меняются;
#    - исходники OEW (../main.c) в сборку не входят.
#
#  Цели:
#    make -C bm1109          — прошивка (ELF/BIN/HEX) + size
#    make -C bm1109 test     — host-тесты расчётного ядра
#    make -C bm1109 clean    — удалить bm1109/build
# ============================================================================

ROOT       := ..
TARGET     := bm1109_rl
BUILD_DIR  := build

TOOLCHAIN_PATH ?= C:/Program Files (x86)/Arm GNU Toolchain arm-none-eabi/13.2 Rel1/bin
ifeq ($(OS),Windows_NT)
export PATH := $(TOOLCHAIN_PATH);$(PATH)
endif

CC       := arm-none-eabi-gcc
AS       := arm-none-eabi-gcc -x assembler-with-cpp
OBJCOPY  := arm-none-eabi-objcopy
SIZE     := arm-none-eabi-size

CMSIS_DEVICE_DIR ?= C:/Users/190/STM32CubeG4/Drivers/CMSIS/Device/ST/STM32G4xx
CMSIS_CORE_DIR   ?= C:/Users/190/STM32CubeG4/Drivers/CMSIS/Core/Include

INCLUDES  := -I. -I$(CMSIS_DEVICE_DIR)/Include -I$(CMSIS_CORE_DIR)
CPU_FLAGS := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard

CFLAGS  := $(CPU_FLAGS) -Os $(INCLUDES) -Wall -Wextra -Wno-unused-parameter
CFLAGS  += -DSTM32G474xx -DUSE_HAL_DRIVER -ffunction-sections -fdata-sections -std=c99

LDFLAGS  := $(CPU_FLAGS) -T$(ROOT)/linker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map
LDFLAGS  += -Wl,--gc-sections -Wl,--start-group -lc -lm -Wl,--end-group
LDFLAGS  += -specs=nano.specs -specs=nosys.specs

C_SOURCES   := bm1109_rl_measure.c bm1109_measure_core.c $(ROOT)/system_stm32g4xx.c
ASM_SOURCES := $(ROOT)/startup_stm32g474xx.s

C_OBJECTS   := $(addprefix $(BUILD_DIR)/,$(notdir $(C_SOURCES:.c=.o)))
ASM_OBJECTS := $(addprefix $(BUILD_DIR)/,$(notdir $(ASM_SOURCES:.s=.o)))
OBJECTS     := $(C_OBJECTS) $(ASM_OBJECTS)

vpath %.c . $(ROOT)
vpath %.s $(ROOT)

# Host-компилятор для тестов ядра: PATH, иначе MinGW, если он есть.
HOST_CC  ?= $(firstword $(wildcard C:/MinGW/bin/gcc.exe) gcc)
TEST_BIN := $(BUILD_DIR)/test_core.exe

all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).bin $(BUILD_DIR)/$(TARGET).hex
	@echo "BM1109 build complete: $(BUILD_DIR)/$(TARGET).elf"
	$(SIZE) $(BUILD_DIR)/$(TARGET).elf

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) $(ROOT)/linker.ld
	@mkdir -p $(BUILD_DIR)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

$(BUILD_DIR)/%.o: %.c
	@mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.s
	@mkdir -p $(BUILD_DIR)
	$(AS) $(ASFLAGS) -c $< -o $@

$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O binary $< $@

$(BUILD_DIR)/$(TARGET).hex: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O ihex $< $@

# Тесты расчётного ядра на host-машине (без STM32).
test:
	@mkdir -p $(BUILD_DIR)
	$(HOST_CC) -std=c99 -Wall -Wextra -O1 -I. test/test_core.c bm1109_measure_core.c -lm -o $(TEST_BIN)
	./$(TEST_BIN)

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all test clean
