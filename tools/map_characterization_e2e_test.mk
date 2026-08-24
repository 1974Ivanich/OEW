CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror
CPPFLAGS ?= -Isrc -Itools -Itests

TARGET := tests/map_characterization_e2e_test.exe

$(TARGET): tests/map_characterization_e2e_test.c \
           tools/map_characterization_adapter.c
	$(CC) $(CFLAGS) $(CPPFLAGS) $^ -o $@

map-characterization-e2e-test: $(TARGET)
	./$(TARGET)

.PHONY: map-characterization-e2e-test
