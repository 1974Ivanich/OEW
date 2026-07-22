# Makefile for STM32G474RE OEW Motor - Phase Resistance Measurement
# Uses ARM GCC Toolchain

# Toolchain
TOOLCHAIN = arm-none-eabi-
CC = "$(TOOLCHAIN)gcc"
AS = "$(TOOLCHAIN)gcc" -x assembler-with-cpp
OBJCOPY = "$(TOOLCHAIN)objcopy"
SIZE = "$(TOOLCHAIN)size"

# CMSIS paths
CMSIS_DEVICE_DIR = C:/Users/190/STM32CubeG4/Drivers/CMSIS/Device/ST/STM32G4xx
CMSIS_CORE_DIR = C:/Users/190/STM32CubeG4/Drivers/CMSIS/Core/Include
HAL_DIR = C:/Users/190/STM32CubeG4/Drivers/STM32G4xx_HAL_Driver

# Toolchain path
TOOLCHAIN_PATH = "C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.2 Rel1\bin"
export PATH := $(TOOLCHAIN_PATH);$(PATH)

# Target
TARGET = firmware

# Build directory
BUILD_DIR = build

# Sources
C_SOURCES = \
main.c \
stm32g4xx_hal.c \
stm32g4xx_hal_cortex.c \
stm32g4xx_hal_gpio.c \
stm32g4xx_hal_rcc.c \
stm32g4xx_hal_rcc_ex.c \
stm32g4xx_hal_tim.c \
stm32g4xx_hal_tim_ex.c \
stm32g4xx_hal_flash.c \
stm32g4xx_hal_flash_ex.c \
stm32g4xx_hal_pwr.c \
stm32g4xx_hal_pwr_ex.c \
stm32g4xx_hal_dma.c \
stm32g4xx_hal_dma_ex.c \
system_stm32g4xx.c

ASM_SOURCES = \
startup_stm32g474xx.s

# Includes
INCLUDES = \
-I. \
-I$(CMSIS_DEVICE_DIR)/Include \
-I$(CMSIS_CORE_DIR) \
-I$(HAL_DIR)/Inc \
-I$(CMSIS_DEVICE_DIR)

# CPU and FPU flags
CPU_FLAGS = -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard

# Optimization
OPT = -Os

# C flags
CFLAGS = $(CPU_FLAGS) $(OPT) $(INCLUDES) -Wall -Wextra -Wno-unused-parameter
CFLAGS += -DSTM32G474xx -DUSE_HAL_DRIVER
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -std=c99

# Linker flags - add -u _printf_float for sprintf %f support
LDFLAGS = $(CPU_FLAGS) -Tlinker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map
LDFLAGS += -Wl,--gc-sections -Wl,--start-group -lc -lm -Wl,--end-group
LDFLAGS += -specs=nano.specs -specs=nosys.specs
LDFLAGS += -u _printf_float

# Objects
C_OBJECTS = $(addprefix $(BUILD_DIR)/, $(C_SOURCES:.c=.o))
ASM_OBJECTS = $(addprefix $(BUILD_DIR)/, $(ASM_SOURCES:.s=.o))
OBJECTS = $(C_OBJECTS) $(ASM_OBJECTS)

# Default target: build
all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).bin $(BUILD_DIR)/$(TARGET).hex
	@echo "Build complete!"
	$(SIZE) $(BUILD_DIR)/$(TARGET).elf

# Link
$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) linker.ld
	@mkdir -p $(BUILD_DIR)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

# Compile C
$(BUILD_DIR)/%.o: %.c
	@mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

# Compile ASM
$(BUILD_DIR)/%.o: %.s
	@mkdir -p $(BUILD_DIR)
	$(AS) $(ASFLAGS) -c $< -o $@

# Generate binary
$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O binary $< $@

# Generate hex
$(BUILD_DIR)/$(TARGET).hex: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O ihex $< $@

# Clean
clean:
	rm -rf $(BUILD_DIR)

# Flash
flash: $(BUILD_DIR)/$(TARGET).bin
	STM32_Programmer_CLI -c port=SWD -w $(BUILD_DIR)/$(TARGET).bin 0x08000000 -v -rst

.PHONY: all clean flash
