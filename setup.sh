#!/usr/bin/env bash
# jobd-ai — local machine setup
#
# Use this if you're NOT in a Codespace/devcontainer — e.g. running jobd-ai
# directly on your own Mac/Linux box. Installs the same tools the devcontainer
# gives you for free, so `docker compose up` behaves identically everywhere.
#
# Usage:
#   ./setup.sh check        # show what's missing, install nothing (the M0 gate)
#   ./setup.sh docker       # Docker + Compose
#   ./setup.sh python       # Python 3.12 + venv
#   ./setup.sh claude       # Claude Code CLI
#   ./setup.sh aws          # AWS CLI v2
#   ./setup.sh terraform    # Terraform
#   ./setup.sh gh           # GitHub CLI
#   ./setup.sh node         # Node.js (optional — Claude Code plugin tooling)
#   ./setup.sh all          # everything above
#   ./setup.sh project      # venv + pip install -e '.[dev]' (run after 'all')
#
# 'check' exits non-zero if anything is missing, so it works as a gate in CI.
# See docs/build-guide.md, milestone M0.

set -euo pipefail

OS="$(uname -s)"
ARCH="$(uname -m)"
IS_MAC=false; IS_LINUX=false
[[ "$OS" == "Darwin" ]] && IS_MAC=true
[[ "$OS" == "Linux" ]] && IS_LINUX=true

# Distro identity, read in a subshell so /etc/os-release can't clobber our vars.
DISTRO_ID=""; DISTRO_CODENAME=""
if $IS_LINUX && [[ -r /etc/os-release ]]; then
  DISTRO_ID="$(. /etc/os-release && echo "${ID:-}")"
  DISTRO_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
fi

log()  { echo -e "\033[1;36m==>\033[0m $*"; }
warn() { echo -e "\033[1;33m!!\033[0m $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Is the Compose v2 plugin actually usable? `have docker` does not imply this.
have_compose() { docker compose version >/dev/null 2>&1; }

require_brew() {
  if ! have brew; then
    log "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || return 1
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
  fi
}

# Every installer below returns non-zero on failure rather than relying on
# `set -e`. errexit is suspended inside `if`-conditions, which is exactly how
# 'all' invokes them — so failures must be propagated explicitly.

install_docker() {
  if have docker; then log "Docker already installed ($(docker --version))"; return 0; fi
  if $IS_MAC; then
    require_brew || return 1
    log "Installing Docker Desktop via brew cask..."
    brew install --cask docker || return 1
    warn "Open Docker Desktop once manually to finish setup and start the daemon."
  elif $IS_LINUX; then
    log "Installing Docker Engine (official convenience script)..."
    curl -fsSL https://get.docker.com | sh || return 1
    sudo usermod -aG docker "$USER" || return 1
    warn "Log out/in (or 'newgrp docker') for group membership to take effect."
  else
    warn "Unsupported OS for auto-install. See https://docs.docker.com/get-docker/"
    return 1
  fi
}

install_python() {
  if have python3 && python3 -c 'import sys; exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
    log "Python 3.12+ already installed ($(python3 --version))"
    return 0
  fi
  if $IS_MAC; then
    require_brew || return 1
    log "Installing Python 3.12 via brew..."
    brew install python@3.12 || return 1
  elif $IS_LINUX; then
    sudo apt-get update -qq || return 1
    if apt-cache show python3.12 >/dev/null 2>&1; then
      log "Installing Python 3.12 via apt..."
      sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip || return 1
    elif [[ "$DISTRO_ID" == "ubuntu" ]]; then
      # deadsnakes is an Ubuntu PPA — it has no Debian equivalent.
      log "Installing Python 3.12 via the deadsnakes PPA..."
      sudo apt-get install -y -qq software-properties-common || return 1
      sudo add-apt-repository -y ppa:deadsnakes/ppa || return 1
      sudo apt-get update -qq || return 1
      sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip || return 1
    else
      warn "No python3.12 package for ${DISTRO_ID:-this distro}, and deadsnakes is Ubuntu-only."
      warn "Use pyenv (https://github.com/pyenv/pyenv) or the devcontainer, which ships 3.12."
      return 1
    fi
  fi
}

install_claude() {
  if have claude; then log "Claude Code already installed ($(claude --version))"; return 0; fi
  log "Installing Claude Code CLI..."
  curl -fsSL https://claude.ai/install.sh | bash || return 1
  warn "Restart your shell (or 'source ~/.bashrc'/'~/.zshrc'), then run 'claude' to authenticate."
}

install_aws() {
  if have aws; then log "AWS CLI already installed ($(aws --version))"; return 0; fi
  log "Installing AWS CLI v2..."
  if $IS_MAC; then
    curl -fsSL "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o /tmp/AWSCLIV2.pkg || return 1
    sudo installer -pkg /tmp/AWSCLIV2.pkg -target / || return 1
  elif $IS_LINUX; then
    local url="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
    [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]] && url="https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip"
    sudo apt-get install -y -qq unzip || return 1
    curl -fsSL "$url" -o /tmp/awscliv2.zip || return 1
    (cd /tmp && unzip -oq awscliv2.zip && sudo ./aws/install --update) || return 1
  fi
}

install_terraform() {
  if have terraform; then log "Terraform already installed ($(terraform --version | head -1))"; return 0; fi
  log "Installing Terraform..."
  if $IS_MAC; then
    require_brew || return 1
    brew tap hashicorp/tap || return 1
    brew install hashicorp/tap/terraform || return 1
  elif $IS_LINUX; then
    # Note: no software-properties-common here. It exists only to provide
    # add-apt-repository for PPAs, HashiCorp ships a plain apt repo, and the
    # package is absent on Debian 13+ — depending on it broke this installer.
    local codename="${DISTRO_CODENAME:-}"
    [[ -z "$codename" ]] && have lsb_release && codename="$(lsb_release -cs)"
    if [[ -z "$codename" ]]; then
      warn "Could not determine distro codename for the HashiCorp apt repo."
      return 1
    fi
    sudo apt-get update -qq || return 1
    sudo apt-get install -y -qq gnupg wget curl || return 1
    wget -qO- https://apt.releases.hashicorp.com/gpg \
      | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg || return 1
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${codename} main" \
      | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null || return 1
    sudo apt-get update -qq || return 1
    sudo apt-get install -y -qq terraform || return 1
  fi
}

install_gh() {
  if have gh; then log "GitHub CLI already installed ($(gh --version | head -1))"; return 0; fi
  log "Installing GitHub CLI..."
  if $IS_MAC; then
    require_brew || return 1
    brew install gh || return 1
  elif $IS_LINUX; then
    sudo apt-get update -qq || return 1
    sudo apt-get install -y -qq curl || return 1
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg status=none || return 1
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null || return 1
    sudo apt-get update -qq || return 1
    sudo apt-get install -y -qq gh || return 1
  fi
}

# Node is not a jobd-ai dependency — the project is Python-only. It is here
# because Claude Code ships as a standalone binary with no bundled runtime, so
# JS-based statusline/plugin tooling has nothing to run on after a rebuild.
# Optional by design: check() reports it but does not fail on it.
install_node() {
  if have node; then log "Node already installed ($(node --version))"; return 0; fi
  log "Installing Node.js..."
  if $IS_MAC; then
    require_brew || return 1
    brew install node || return 1
  elif $IS_LINUX; then
    sudo apt-get update -qq || return 1
    sudo apt-get install -y -qq nodejs || return 1
  fi
}

setup_project() {
  log "Creating virtualenv and installing project (editable, dev extras)..."
  python3.12 -m venv .venv 2>/dev/null || python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  # Since M2 the scaffold exists, so a failure here is a real failure, not the
  # expected absence of a pyproject. Do not soften it back to a warning.
  pip install -e '.[dev]' || { warn "editable install failed — see error above."; return 1; }
  activate_venv_on_login
  log "Activate with: source .venv/bin/activate"
}

# Put the venv on the PATH of every interactive shell.
#
# Without this, a rebuilt container has a working venv that nothing is using:
# `jobd` is not on the PATH, and the natural fix — `pip install -e .` outside
# the venv — installs a second copy against the system interpreter. That copy
# wins the PATH race and is missing every dependency added since it was made,
# so `jobd` fails with a ModuleNotFoundError for a package that is, in fact,
# installed. Cheaper to activate automatically than to debug that twice.
activate_venv_on_login() {
  local rc="${HOME}/.bashrc" marker="# jobd-ai: activate the project venv"
  [ -f "$rc" ] || touch "$rc"
  if grep -qF "$marker" "$rc"; then
    return 0
  fi
  {
    printf '\n%s\n' "$marker"
    # Guarded on the file existing so a shell opened before ./setup.sh project
    # (or after a `rm -rf .venv`) still starts, rather than erroring on login.
    printf '%s\n' '[ -f /workspaces/jobd-ai/.venv/bin/activate ] && . /workspaces/jobd-ai/.venv/bin/activate'
  } >>"$rc"
  log "Added venv activation to ${rc}."
}

install_all() {
  local failed=() step
  for step in docker python claude aws terraform gh node; do
    if ! "install_${step}"; then
      warn "FAILED: ${step}"
      failed+=("${step}")
    fi
  done
  if (( ${#failed[@]} > 0 )); then
    echo
    warn "Failed: ${failed[*]}"
    warn "Re-run individually for the full error, e.g.: ./setup.sh ${failed[0]}"
    return 1
  fi
  log "Done. Next: ./setup.sh project, then 'docker compose up -d db' and 'jobd --help'."
}

# Can we open a TCP connection to the Postgres in DATABASE_URL?
check_db() {
  [[ -n "${DATABASE_URL:-}" ]] || return 2
  have python3 || return 2
  python3 - <<'PY' 2>/dev/null
import os, socket, sys
from urllib.parse import urlparse
u = urlparse(os.environ["DATABASE_URL"])
try:
    socket.create_connection((u.hostname, u.port or 5432), timeout=3).close()
except OSError:
    sys.exit(1)
PY
}

check() {
  local missing=0 tool
  log "Checking local environment..."

  for tool in docker python3 claude aws terraform gh; do
    if have "$tool"; then
      echo "  [x] $tool"
    else
      echo "  [ ] $tool  (missing)"
      missing=1
    fi
  done

  # Optional: not a jobd-ai dependency, so it never sets `missing`.
  if have node; then
    echo "  [x] node (optional — Claude Code plugin tooling)"
  else
    echo "  [-] node (optional, absent — JS-based CC plugins will not run)"
  fi

  if have_compose; then
    echo "  [x] docker compose"
  else
    echo "  [ ] docker compose  (missing)"
    missing=1
  fi

  # M0 gate part 3: Postgres reachable at DATABASE_URL.
  local db_status=0
  check_db || db_status=$?
  case $db_status in
    0) echo "  [x] postgres (DATABASE_URL)" ;;
    2) echo "  [-] postgres (DATABASE_URL unset — skipped)" ;;
    *) echo "  [ ] postgres (DATABASE_URL set but unreachable — try 'docker compose up -d db')"
       missing=1 ;;
  esac

  echo
  if (( missing )); then
    echo "Run: ./setup.sh all      # install everything missing"
    echo "     ./setup.sh project  # venv + pip install -e '.[dev]'"
    return 1
  fi
  log "Environment complete."
}

case "${1:-}" in
  check)     check ;;
  docker)    install_docker ;;
  python)    install_python ;;
  claude)    install_claude ;;
  aws)       install_aws ;;
  terraform) install_terraform ;;
  gh)        install_gh ;;
  node)      install_node ;;
  project)   setup_project ;;
  all)       install_all ;;
  "")
    log "No argument given — defaulting to 'all'."
    install_all
    ;;
  *)
    echo "usage: ./setup.sh {check|docker|python|claude|aws|terraform|gh|node|project|all}"
    exit 1
    ;;
esac
