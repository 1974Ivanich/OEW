CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror
CPPFLAGS += -I./src -DOEW_HOST_TEST=1 -DOEW_MAP_SYNTHETIC_PROFILE=1

.PHONY: map-capture-profile-test clean

map-capture-profile-test:
	$(CC) $(CFLAGS) $(CPPFLAGS) src/map_capture_profiles.c tests/map_capture_profile_test.c -o tests/map_capture_profile_test.exe
	./tests/map_capture_profile_test.exe

clean:
	rm -f tests/map_capture_profile_test.exe
