CC ?= cc
PYTHON ?= python3
CFLAGS ?= -O3 -std=c17 -Wall -Wextra -Wpedantic
CPPFLAGS += -Ibuild -Inative/include -Inative/vendor/cjson -Inative/vendor/dr_libs
LDLIBS += -lm
SOURCES = native/main.c native/common.c native/catalog.c native/dsp.c native/dsp_kernel.c native/vendor/cjson/cJSON.c
UNAME := $(shell uname -s)
ifeq ($(UNAME),Darwin)
LDLIBS += -lsqlite3
SHARED = -dynamiclib
LIBEXT = dylib
APPLE_TARGET = build/music-apple
else
CPPFLAGS += $(shell pkg-config --cflags sqlite3 openssl)
LDLIBS += $(shell pkg-config --libs sqlite3 openssl)
SHARED = -shared -fPIC
LIBEXT = so
endif

.PHONY: all test sanitize clean
all: build/music build/libmusicdsp.$(LIBEXT) $(APPLE_TARGET)
build/schema.h: $(wildcard src/music_understanding/migrations/*.sql) scripts/embed_schema.py
	$(PYTHON) scripts/embed_schema.py
build/music: build/schema.h $(SOURCES) native/include/music.h native/vendor/dr_libs/dr_wav.h
	mkdir -p build
	$(CC) $(CFLAGS) $(CPPFLAGS) $(SOURCES) $(LDLIBS) -o $@
build/libmusicdsp.$(LIBEXT): native/dsp_kernel.c
	mkdir -p build
	$(CC) $(CFLAGS) $(SHARED) $< -lm -o $@
build/music-apple: native/apple/audio.swift
	mkdir -p build
	xcrun swiftc -O -swift-version 5 -parse-as-library -target $(shell uname -m)-apple-macosx12.0 $< -o $@
sanitize: build/schema.h
	mkdir -p build
	$(CC) -g -std=c17 -fsanitize=address,undefined $(CPPFLAGS) $(SOURCES) $(LDLIBS) -o build/music-sanitize
test: all sanitize
	$(PYTHON) -m pytest tests -q
clean:
	rm -f build/music build/music-apple build/music-sanitize build/libmusicdsp.dylib build/libmusicdsp.so
