CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror

.PHONY: map-capture-profile-test map-capture-profile-default-test clean

map-capture-profile-test:
	$(CC) $(CFLAGS) -I./src -DOEW_HOST_TEST=1 -DOEW_MAP_SYNTHETIC_PROFILE=1 src/map_capture_profiles.c src/map_measurement_reference.c tests/map_capture_profile_test.c -o tests/map_capture_profile_test.exe
	./tests/map_capture_profile_test.exe

map-capture-profile-default-test:
	$(CC) $(CFLAGS) -I./src src/map_capture_profiles.c src/map_measurement_reference.c tests/map_capture_profile_default_test.c -o tests/map_capture_profile_default_test.exe
	./tests/map_capture_profile_default_test.exe

map-capture-board-profile-test:
	$(CC) $(CFLAGS) -I./src -DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 src/map_capture_profiles.c src/map_measurement_reference.c tests/map_capture_board_profile_test.c -o tests/map_capture_board_profile_test.exe
	./tests/map_capture_board_profile_test.exe

clean:
	rm -f tests/map_capture_profile_test.exe tests/map_capture_profile_default_test.exe tests/map_capture_board_profile_test.exe
