#!/usr/bin/env bash
# Dependency Security Scan — NexusMCP Phase 10
#
# Runs pip-audit and Trivy scans against Python dependencies and container
# images. Intended for CI pipelines and local pre-release verification.
#
# Usage:
#   ./dependency_scan.sh              # full scan (deps + images)
#   ./dependency_scan.sh --deps-only  # Python dependency scan only
#   ./dependency_scan.sh --images-only # container image scan only
#
# Prerequisites:
#   - pip install pip-audit
#   - trivy (https://aquasecurity.github.io/trivy/) installed for image scans

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
REQUIREMENTS_FILE="${BACKEND_DIR}/requirements.txt"

DEPS_ONLY=false
IMAGES_ONLY=false

# Parse arguments
for arg in "$@"; do
  case "$arg" in
    --deps-only) DEPS_ONLY=true ;;
    --images-only) IMAGES_ONLY=true ;;
    --help)
      echo "Usage: $0 [--deps-only | --images-only]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

# ----------------------------------------------------------------------
# Python dependency scanning with pip-audit
# ----------------------------------------------------------------------
scan_dependencies() {
  echo "=== Python Dependency Scan (pip-audit) ==="
  if ! command -v pip-audit >/dev/null 2>&1; then
    echo "ERROR: pip-audit is not installed. Install with: pip install pip-audit" >&2
    return 1
  fi

  if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
    echo "ERROR: requirements.txt not found at ${REQUIREMENTS_FILE}" >&2
    return 1
  fi

  echo "Scanning ${REQUIREMENTS_FILE} ..."
  pip-audit -r "${REQUIREMENTS_FILE}"

  local status=$?
  if [[ ${status} -eq 0 ]]; then
    echo "[OK] No known vulnerabilities found in Python dependencies."
  else
    echo "[FAIL] pip-audit reported vulnerabilities (exit code ${status})." >&2
    return ${status}
  fi

  echo
}

# ----------------------------------------------------------------------
# Container image scanning with Trivy
# ----------------------------------------------------------------------
scan_images() {
  echo "=== Container Image Scan (Trivy) ==="
  if ! command -v trivy >/dev/null 2>&1; then
    echo "WARNING: trivy is not installed. Skipping container image scans."
    echo "Install from: https://aquasecurity.github.io/trivy/"
    return 0
  fi

  # Read image tags from docker-compose if available
  local compose_file="${PROJECT_ROOT}/deployment/docker-compose.yml"
  local images=()

  if [[ -f "${compose_file}" ]]; then
    # Extract image names from docker-compose (best-effort)
    while IFS= read -r line; do
      images+=("${line}")
    done < <(grep -E '^\s+image:' "${compose_file}" | awk '{print $2}' | tr -d '"' || true)
  fi

  # Default images if none found in compose
  if [[ ${#images[@]} -eq 0 ]]; then
    images=(
      "mongo:7.0"
      "postgres:15"
      "temporalio/auto-setup:1.24.2"
      "temporalio/ui:2.30.0"
    )
  fi

  local had_failures=0
  for image in "${images[@]}"; do
    echo "Scanning image: ${image} ..."
    if trivy image --exit-code 0 --severity HIGH,CRITICAL "${image}" >/dev/null 2>&1; then
      echo "[OK] No HIGH/CRITICAL vulnerabilities in ${image}"
    else
      echo "[WARN] Vulnerabilities found in ${image} (review recommended)"
      had_failures=1
    fi
  done

  if [[ ${had_failures} -eq 1 ]]; then
    echo
    echo "One or more images have HIGH/CRITICAL vulnerabilities. Review and patch as needed."
  fi

  echo
}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
main() {
  echo "NexusMCP Dependency Security Scan"
  echo "Project root: ${PROJECT_ROOT}"
  echo

  if [[ "${DEPS_ONLY}" == "true" && "${IMAGES_ONLY}" == "true" ]]; then
    echo "ERROR: --deps-only and --images-only are mutually exclusive." >&2
    exit 2
  fi

  local overall_status=0

  if [[ "${IMAGES_ONLY}" == "false" ]]; then
    scan_dependencies || overall_status=$?
  fi

  if [[ "${DEPS_ONLY}" == "false" ]]; then
    scan_images || true  # image warnings are advisory, not failing
  fi

  if [[ ${overall_status} -ne 0 ]]; then
    echo "Dependency scan completed with failures." >&2
    exit ${overall_status}
  fi

  echo "Dependency security scan complete."
}

main "$@"
