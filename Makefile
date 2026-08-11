# Makefile for STM32G474RE OEW Motor FOC
TOOLCHAIN = arm-none-eabi-
CC = $(TOOLCHAIN)gcc
AS = $(TOOLCHAIN)gcc -x assembler-with-cpp
OBJCOPY = $(TOOLCHAIN)objcopy
SIZE = $(TOOLCHAIN)size

CMSIS_DIR = Drivers/CMSIS
CMSIS_DEVICE_DIR = $(CMSIS_DIR)/Device/ST/STM32G4xx
CMSIS_CORE_DIR = $(CMSIS_DIR)/Include

TOOLCHAIN_PATH = C:\ST\STM32CubeCLT_1.22.0\GNU-tools-for-STM32\bin
MAKE_PATH = C:\ST\STM32CubeCLT_1.22.0\Make\bin
export PATH := $(TOOLCHAIN_PATH);$(MAKE_PATH);$(PATH)

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
$(SRC_DIR)/voltage_manager.c \
$(SRC_DIR)/vf_start.c \
$(SRC_DIR)/protect.c \
$(SRC_DIR)/autotune.c \
$(SRC_DIR)/encoder.c \
$(SRC_DIR)/vf_control.c \
$(SRC_DIR)/swo.c

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
	"C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe" -c port=SWD mode=UR -w $(BUILD_DIR)/$(TARGET).bin 0x08000000 -v -rst

.PHONY: all clean flash test test-hosted test-qemu

# ── Тесты FOC/Vf математики (hosted + QEMU, без железа) ────────────────────
HOSTED_GCC = gcc
ARM_GCC = arm-none-eabi-gcc
QEMU = C:/ST/xpack-qemu-arm-9.2.4-1/bin/qemu-system-arm.exe
MOCK_INC = -I tests/mocks
TEST_COMMON = tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c

test: test-hosted test-qemu
	@echo "=== TESTS OK ==="

test-hosted: tests/foc_test_hosted.exe tests/vf_test_hosted.exe
	@echo "--- FOC math (hosted) ---"; ./tests/foc_test_hosted.exe
	@echo "--- V/f control (hosted) ---"; ./tests/vf_test_hosted.exe

test-qemu: tests/foc_test_qemu.elf tests/vf_test_qemu.elf
	@echo "--- FOC math (QEMU) ---"; $(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/foc_test_qemu.elf 2>&1 | tail -3
	@echo "--- V/f control (QEMU) ---"; $(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/vf_test_qemu.elf 2>&1 | tail -3

tests/foc_test_hosted.exe: tests/foc_math_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -lm -o $@

tests/vf_test_hosted.exe: tests/vf_control_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/vf_control.c -o $@

tests/foc_test_qemu.elf: tests/foc_math_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@

tests/vf_test_qemu.elf: tests/vf_control_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/vf_control.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@
