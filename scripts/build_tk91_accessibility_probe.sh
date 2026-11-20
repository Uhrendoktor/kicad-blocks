#!/usr/bin/env bash
# Linux-only feasibility probe: build CPython 3.14 against Tcl/Tk 9.1 with ATK.
# This is not a production installer; it validates the runtime KiCad could use.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${1:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/kicad-blocks-tk91-probe}"
TCL_PREFIX="$BUILD_ROOT/tcltk"
PYTHON_PREFIX="$BUILD_ROOT/cpython"
TCL_SOURCE="$BUILD_ROOT/tcl"
TK_SOURCE="$BUILD_ROOT/tk"
PYTHON_SOURCE="$BUILD_ROOT/Python-3.14.4"
JOBS="${TK91_BUILD_JOBS:-2}"
PYTHON_VERSION="3.14.4"
TCL_TAG="core-9-1-0"
TK_TAG="core-9-1-0"

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"

sudo apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 update
sudo apt-get install -y --no-install-recommends \
  build-essential ca-certificates curl git pkg-config xz-utils \
  libatk1.0-dev libatk-bridge2.0-dev libdbus-1-dev libglib2.0-dev \
  libx11-dev libxext-dev libxft-dev libxss-dev libcups2-dev \
  libharfbuzz-dev libfontconfig1-dev libfreetype6-dev libtommath-dev \
  libbz2-dev libffi-dev libgdbm-dev libgdbm-compat-dev liblzma-dev \
  libncursesw5-dev libreadline-dev libsqlite3-dev libssl-dev libuuid-dev \
  zlib1g-dev libexpat1-dev xvfb dbus-daemon dbus-bin at-spi2-core

echo "::group::Fetch pinned Tcl/Tk 9.1.0 sources"
git clone --quiet --depth 1 --branch "$TCL_TAG" https://github.com/tcltk/tcl.git "$TCL_SOURCE"
git clone --quiet --depth 1 --branch "$TK_TAG" https://github.com/tcltk/tk.git "$TK_SOURCE"
echo "Tcl tag: $(git -C "$TCL_SOURCE" describe --tags --exact-match)"
echo "Tk tag:  $(git -C "$TK_SOURCE" describe --tags --exact-match)"
echo "::endgroup::"

echo "::group::Build Tcl 9.1.0"
(
  cd "$TCL_SOURCE/unix"
  ./configure --prefix="$TCL_PREFIX" --enable-shared --enable-threads
  make -j"$JOBS"
  make install
)
echo "::endgroup::"

echo "::group::Build Tk 9.1.0 with the ATK bridge"
touch "$TK_SOURCE/doc/man.macros" "$TK_SOURCE/generic/tkStubInit.c"
(
  cd "$TK_SOURCE/unix"
  ./configure --prefix="$TCL_PREFIX" --with-tcl="$TCL_SOURCE/unix" --enable-shared --enable-threads
  if ! grep -Eq '^#define HAVE_ATK 1$' tkConfig.h; then
    echo "::error::Tk 9.1 was configured without HAVE_ATK; refusing to test a non-accessible build."
    grep -E 'ATK|Atk-bridge' config.log || true
    exit 1
  fi
  make -j"$JOBS" binaries libraries
  make install
)
echo "::endgroup::"

export PKG_CONFIG_PATH="$TCL_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
TCL_VERSION="$(pkg-config --modversion tcl)"
TK_VERSION="$(pkg-config --modversion tk)"
if [[ "$TCL_VERSION" != 9.1.0 || "$TK_VERSION" != 9.1.0 ]]; then
  echo "::error::Expected Tcl/Tk 9.1.0 pkg-config modules, got Tcl=$TCL_VERSION Tk=$TK_VERSION."
  pkg-config --list-all | grep -E '(^tcl|^tk)' || true
  exit 1
fi
export TCLTK_CFLAGS="$(pkg-config --cflags tcl tk)"
export TCLTK_LIBS="$(pkg-config --libs tk)"
export CPPFLAGS="-I$TCL_PREFIX/include ${CPPFLAGS:-}"
export LDFLAGS="-L$TCL_PREFIX/lib -Wl,-rpath,$TCL_PREFIX/lib -Wl,-rpath,$PYTHON_PREFIX/lib ${LDFLAGS:-}"
export LD_LIBRARY_PATH="$TCL_PREFIX/lib:$PYTHON_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

echo "::group::Build CPython $PYTHON_VERSION against Tcl/Tk 9.1.0"
curl --fail --location --retry 3 \
  "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tar.xz" \
  --output "$BUILD_ROOT/Python-$PYTHON_VERSION.tar.xz"
tar -xJf "$BUILD_ROOT/Python-$PYTHON_VERSION.tar.xz" -C "$BUILD_ROOT"
(
  cd "$PYTHON_SOURCE"
  TCLTK_CFLAGS="$TCLTK_CFLAGS" TCLTK_LIBS="$TCLTK_LIBS" \
    ./configure --prefix="$PYTHON_PREFIX" --enable-shared --with-ensurepip=install
  make -j"$JOBS"
  make install
)
PYTHON="$PYTHON_PREFIX/bin/python3.14"
"$PYTHON" --version
echo "CPython Tcl/Tk build flags: TCLTK_CFLAGS=$TCLTK_CFLAGS TCLTK_LIBS=$TCLTK_LIBS"
echo "::endgroup::"

echo "::group::Create a KiCad-style plugin venv and install the AT-SPI probe"
"$PYTHON" -m venv "$BUILD_ROOT/plugin-venv"
VENV_PYTHON="$BUILD_ROOT/plugin-venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install 'xa11y==0.16.0'
xvfb-run -a "$VENV_PYTHON" -c \
  'import _tkinter, tkinter; root=tkinter.Tk(); version=root.tk.call("package","provide","Tk"); print("Tk package:", version, "Tcl:", tkinter.TclVersion, "Tk:", tkinter.TkVersion, "_tkinter:", _tkinter.__file__); assert version.startswith("9.1."); root.destroy()'
echo "::endgroup::"

echo "::group::Query real Tk widgets through AT-SPI"
dbus-run-session -- bash "$ROOT_DIR/scripts/run_with_atspi.sh" \
  xvfb-run -a -s "-screen 0 1280x800x24" \
  "$VENV_PYTHON" "$ROOT_DIR/tests/test_tk91_accessibility_probe.py"
echo "::endgroup::"

echo "Tk 9.1 accessibility feasibility probe passed. Runtime build directory: $BUILD_ROOT"
