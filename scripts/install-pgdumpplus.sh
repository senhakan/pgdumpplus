#!/usr/bin/env bash
# Install a precompiled pg_dumpplus package on a supported Linux host.
set -Eeuo pipefail

readonly REPO="senhakan/pgdumpplus"
readonly API_URL="https://api.github.com/repos/${REPO}/releases/latest"
YES=0
DRY_RUN=0
REQUESTED_VERSION=""
REQUESTED_MAJOR=""
TMP_DIR=""

usage() {
  cat <<'USAGE'
Usage: install-pgdumpplus.sh [options]

Detects the OS and PostgreSQL major version, selects the matching GitHub
Release package, asks for confirmation, installs it, and verifies the binary.

Options:
  --yes, -y             do not prompt; proceed automatically
  --dry-run             detect and print the package, do not download/install
  --version VERSION     install a specific project release (for example 2.1.1)
  --pg-major MAJOR      override PostgreSQL major-version detection (13-18)
  --help, -h            show this help

Supported package targets: Ubuntu 22.04/24.04, Debian 12, Rocky/RHEL/AlmaLinux
8/9/10, x86_64. The client is precompiled; no compiler or Python is required.
USAGE
}

die() { printf 'pg_dumpplus installer: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
}
trap cleanup EXIT

need_command() { command -v "$1" >/dev/null 2>&1 || die "gerekli komut bulunamadı: $1"; }

while (($#)); do
  case "$1" in
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --version) (($# >= 2)) || die "--version için değer eksik"; REQUESTED_VERSION="${2#v}"; shift 2 ;;
    --pg-major) (($# >= 2)) || die "--pg-major için değer eksik"; REQUESTED_MAJOR="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "bilinmeyen seçenek: $1" ;;
  esac
done

[[ "$(uname -m)" == "x86_64" ]] || die "yalnızca x86_64 paketleri yayımlanıyor (mimari: $(uname -m))"
if [[ $EUID -eq 0 ]]; then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || die "root veya sudo yetkisi gerekiyor"
  SUDO=(sudo)
fi

OS_ID=""; OS_VERSION=""; OS_LABEL=""; PACKAGE_FORMAT=""
[[ -r /etc/os-release ]] || die "/etc/os-release bulunamadı"
# shellcheck disable=SC1091
. /etc/os-release
OS_ID="${ID:-}"; OS_VERSION="${VERSION_ID:-}"
case "$OS_ID" in
  ubuntu)
    case "$OS_VERSION" in
      22.04) OS_LABEL="ubuntu2204"; PACKAGE_FORMAT="deb" ;;
      24.04) OS_LABEL="ubuntu2404"; PACKAGE_FORMAT="deb" ;;
      *) die "desteklenmeyen Ubuntu sürümü: $OS_VERSION (22.04 veya 24.04 gerekir)" ;;
    esac ;;
  debian)
    [[ "$OS_VERSION" == 12* ]] || die "desteklenmeyen Debian sürümü: $OS_VERSION (12 gerekir)"
    OS_LABEL="debian12"; PACKAGE_FORMAT="deb" ;;
  rocky|rhel|almalinux)
    EL_MAJOR="${OS_VERSION%%.*}"
    [[ "$EL_MAJOR" =~ ^(8|9|10)$ ]] || die "desteklenmeyen Enterprise Linux sürümü: $OS_VERSION (8/9/10 gerekir)"
    OS_LABEL="el${EL_MAJOR}"; PACKAGE_FORMAT="rpm" ;;
  *) die "desteklenmeyen işletim sistemi: ${OS_ID:-bilinmiyor}. Ubuntu 22.04/24.04, Debian 12 veya Rocky/RHEL/AlmaLinux 8/9/10 kullanın" ;;
esac

detect_pg_major() {
  local value="" psql_bin="" pg_config_bin="" candidate=""
  local candidates=(
    "$(command -v psql 2>/dev/null || true)"
    /usr/pgsql-*/bin/psql
    /usr/lib/postgresql/*/bin/psql
    /usr/local/pgsql/bin/psql
  )
  for candidate in "${candidates[@]}"; do
    [[ -x "$candidate" ]] || continue
    psql_bin="$candidate"
    break
  done
  if [[ -n "$psql_bin" ]]; then
    value="$($psql_bin -X -Atqc 'SHOW server_version_num' 2>/dev/null || true)"
    [[ "$value" =~ ^(1[3-8])[0-9]{4}$ ]] && printf '%s\n' "${BASH_REMATCH[1]}" && return 0
    if [[ $EUID -eq 0 && -x "$(command -v runuser 2>/dev/null || true)" ]] && id postgres >/dev/null 2>&1; then
      value="$(runuser -u postgres -- "$psql_bin" -X -Atqc 'SHOW server_version_num' 2>/dev/null || true)"
      [[ "$value" =~ ^(1[3-8])[0-9]{4}$ ]] && printf '%s\n' "${BASH_REMATCH[1]}" && return 0
    fi
  fi
  local pg_config_candidates=(
    "$(command -v pg_config 2>/dev/null || true)"
    /usr/pgsql-*/bin/pg_config
    /usr/lib/postgresql/*/bin/pg_config
    /usr/local/pgsql/bin/pg_config
  )
  for candidate in "${pg_config_candidates[@]}"; do
    [[ -x "$candidate" ]] || continue
    pg_config_bin="$candidate"
    break
  done
  if [[ -n "$pg_config_bin" ]]; then
    value="$($pg_config_bin --version 2>/dev/null || true)"
    [[ "$value" =~ PostgreSQL[[:space:]]+([0-9]+) ]] && printf '%s\n' "${BASH_REMATCH[1]}" && return 0
  fi
  if [[ "$PACKAGE_FORMAT" == rpm ]] && command -v rpm >/dev/null 2>&1; then
    value="$(rpm -qa 2>/dev/null | sed -n 's/^postgresql\([0-9][0-9]*\)-server-.*/\1/p' | sort -n | tail -1)"
    [[ "$value" =~ ^[0-9]+$ ]] && printf '%s\n' "$value" && return 0
  fi
  if [[ "$PACKAGE_FORMAT" == deb ]] && command -v dpkg-query >/dev/null 2>&1; then
    value="$(dpkg-query -W -f='${Package}\n' 'postgresql-[0-9]*' 2>/dev/null | sed -n 's/^postgresql-\([0-9][0-9]*\)$/\1/p' | sort -n | tail -1)"
    [[ "$value" =~ ^[0-9]+$ ]] && printf '%s\n' "$value" && return 0
  fi
  return 1
}

PG_MAJOR="${REQUESTED_MAJOR:-}"
if [[ -z "$PG_MAJOR" ]]; then PG_MAJOR="$(detect_pg_major || true)"; fi
[[ "$PG_MAJOR" =~ ^(13|14|15|16|17|18)$ ]] || die "PostgreSQL major sürümü algılanamadı; --pg-major 13..18 ile belirtin"

need_command sed
if command -v curl >/dev/null 2>&1; then FETCHER="curl";
elif command -v wget >/dev/null 2>&1; then FETCHER="wget";
else die "curl veya wget gerekli"; fi

fetch() {
  if [[ "$FETCHER" == curl ]]; then curl -fsSL --retry 3 --connect-timeout 15 "$1";
  else wget -qO- "$1"; fi
}
download() {
  if [[ "$FETCHER" == curl ]]; then curl -fL --retry 3 --connect-timeout 15 -o "$2" "$1";
  else wget -q --show-progress -O "$2" "$1"; fi
}

RELEASE_ENDPOINT="$API_URL"
if [[ -n "$REQUESTED_VERSION" ]]; then RELEASE_ENDPOINT="https://api.github.com/repos/${REPO}/releases/tags/v${REQUESTED_VERSION}"; fi
RELEASE_JSON="$(fetch "$RELEASE_ENDPOINT")" || die "GitHub Release bilgisi alınamadı"
TAG="$(printf '%s\n' "$RELEASE_JSON" | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[[ -n "$TAG" ]] || die "latest release tag bulunamadı"
VERSION="${REQUESTED_VERSION:-${TAG#v}}"
[[ "$VERSION" != *[!0-9.]* ]] || die "geçersiz sürüm: $VERSION"

URLS="$(printf '%s\n' "$RELEASE_JSON" | grep -oE '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]+"' | sed -E 's/.*"(https:[^"]+)"/\1/')"
if [[ "$PACKAGE_FORMAT" == deb ]]; then
  ASSET_URL="$(printf '%s\n' "$URLS" | grep -E "/pgdumpplus-${PG_MAJOR}_${VERSION}-[^/]+${OS_LABEL}_amd64\.deb$" | head -1 || true)"
else
  ASSET_URL="$(printf '%s\n' "$URLS" | grep -E "/pgdumpplus-${PG_MAJOR}-${VERSION}-[^/]+\.${OS_LABEL}\.x86_64\.rpm$" | head -1 || true)"
fi
[[ -n "$ASSET_URL" ]] || die "PostgreSQL $PG_MAJOR / $OS_LABEL için release paketi bulunamadı"
ASSET_NAME="${ASSET_URL##*/}"

installed="kurulu değil"
if [[ "$PACKAGE_FORMAT" == deb ]] && command -v dpkg-query >/dev/null 2>&1; then
  installed="$(dpkg-query -W -f='${Version}' "pgdumpplus-${PG_MAJOR}" 2>/dev/null || true)"; [[ -n "$installed" ]] || installed="kurulu değil"
elif [[ "$PACKAGE_FORMAT" == rpm ]] && command -v rpm >/dev/null 2>&1; then
  installed="$(rpm -q --qf '%{VERSION}-%{RELEASE}' "pgdumpplus-${PG_MAJOR}" 2>/dev/null || true)"; [[ -n "$installed" ]] || installed="kurulu değil"
fi

log "İşletim sistemi: ${PRETTY_NAME:-$OS_ID $OS_VERSION} (${OS_LABEL})"
log "PostgreSQL major: $PG_MAJOR"
log "Paket: $ASSET_NAME"
log "Mevcut pgdumpplus-${PG_MAJOR}: $installed"
if ((DRY_RUN)); then log "dry-run: indirme ve kurulum yapılmadı"; exit 0; fi
if ((YES == 0)); then
  [[ -t 0 ]] || die "etkileşimsiz kullanım için --yes verin"
  read -r -p "Bu paketi indirip kurayım mı? [y/N] " answer
  [[ "$answer" =~ ^[YyEe]$ ]] || { log "kullanıcı iptali"; exit 0; }
fi

TMP_DIR="$(mktemp -d -t pgdumpplus-install.XXXXXX)"
PACKAGE_PATH="$TMP_DIR/$ASSET_NAME"
log "Paket indiriliyor: $ASSET_URL"
download "$ASSET_URL" "$PACKAGE_PATH" || die "paket indirilemedi"
[[ -s "$PACKAGE_PATH" ]] || die "indirilen paket boş"

if [[ "$PACKAGE_FORMAT" == deb ]]; then
  "${SUDO[@]}" apt-get install -y "$PACKAGE_PATH"
else
  "${SUDO[@]}" dnf install -y "$PACKAGE_PATH"
fi

VERIFY_BIN="$(command -v "pg_dumpplus-${PG_MAJOR}" || true)"
[[ -n "$VERIFY_BIN" ]] || VERIFY_BIN="/opt/pgdumpplus/${PG_MAJOR}/bin/pg_dumpplus-${PG_MAJOR}"
[[ -x "$VERIFY_BIN" ]] || die "kurulum sonrası pg_dumpplus-${PG_MAJOR} bulunamadı"
"$VERIFY_BIN" --version
"$VERIFY_BIN" --build-info
log "Kurulum ve binary doğrulaması başarılı"
