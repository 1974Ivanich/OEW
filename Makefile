# Makefile for STM32G474RE OEW Motor FOC
TOOLCHAIN = arm-none-eabi-
CC = "$(TOOLCHAIN)gcc"
AS = "$(TOOLCHAIN)gcc" -x assembler-with-cpp
OBJCOPY = "$(TOOLCHAIN)objcopy"
SIZE = "$(TOOLCHAIN)size"

CMSIS_DEVICE_DIR = C:/Users/190/STM32CubeG4/Drivers/CMSIS/Device/ST/STM32G4xx
CMSIS_CORE_DIR = C:/Users/190/STM32CubeG4/Drivers/CMSIS/Core/Include

TOOLCHAIN_PATH = "C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.2 Rel1\bin"
export PATH := $(TOOLCHAIN_PATH);$(PATH)

TARGET = firmware
BUILD_DIR = build

# Source files
SRC_DIR = src
C_SOURCES = \
main.c \
system_stm32g4xx.c \
$(SRC_DIR)/pwm.c \
$(SRC_DIR)/adc.c \
$(SRC_DIR)/uart.c \
$(SRC_DIR)/cordic_math.c \
$(SRC_DIR)/foc.c \
$(SRC_DIR)/observer.c \
$(SRC_DIR)/pll.c \
$(SRC_DIR)/flux_weakening.c \
$(SRC_DIR)/vf_start.c \
$(SRC_DIR)/protect.c

ASM_SOURCES = startup_stm32g474xx.s

# Includes
INCLUDES = \
-I. \
-I$(SRC_DIR) \
-I$(CMSIS_DEVICE_DIR)/Include \
-I$(CMSIS_CORE_DIR)

CPU_FLAGS = -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
OPT = -Os
CFLAGS = $(CPU_FLAGS) $(OPT) $(INCLUDES) -Wall -Wextra -Wno-unused-parameter
CFLAGS += -DSTM32G474xx
CFLAGS += -ffunction-sections -fdata-sections -std=c99
LDFLAGS = $(CPU_FLAGS) -Tlinker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map
LDFLAGS += -Wl,--gc-sections -Wl,--start-group -lc -lm -Wl,--end-group
LDFLAGS += -specs=nano.specs -specs=nosys.specs -u _printf_float

C_OBJECTS = $(addprefix $(BUILD_DIR)/, $(C_SOURCES:.c=.o))
ASM_OBJECTS = $(addprefix $(BUILD_DIR)/, $(ASM_SOURCES:.s=.o))
OBJECTS = $(C_OBJECTS) $(ASM_OBJECTS)

VPATH = $(SRC_DIR)

all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).bin
	@echo "Build complete!"
	$(SIZE) $(BUILD_DIR)/$(TARGET).elf

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) linker.ld
	@mkdir -p $(BUILD_DIR)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

$(BUILD_DIR)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.s
	@mkdir -p $(BUILD_DIR)
	$(AS) $(ASFLAGS) -c $< -o $@

$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O binary $< $@

clean:
	rm -rf $(BUILD_DIR)

flash: $(BUILD_DIR)/$(TARGET).bin
	STM32_Programmer_CLI -c port=SWD -w $(BUILD_DIR)/$(TARGET).bin 0x08000000 -v -rst

.PHONY: all clean flash
