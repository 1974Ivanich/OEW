/* Минимальный linker script для QEMU-теста FOC (bare-metal, без libc). */
ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 128K
  RAM   (rwx): ORIGIN = 0x20000000, LENGTH = 128K
}

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } > FLASH

  .text :
  {
    *(.text*)
    *(.rodata*)
  } > FLASH

  .data :
  {
    *(.data*)
  } > RAM AT > FLASH

  .bss :
  {
    *(.bss*)
    . = ALIGN(4);
  } > RAM

  /* Граница кучи для rdimon (_sbrk) */
  . = ALIGN(4);
  PROVIDE(end = .);
  PROVIDE(_end = .);

  /DISCARD/ :
  {
    *(.ARM.attributes)
  }
}
