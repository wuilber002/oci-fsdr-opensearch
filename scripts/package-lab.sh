#!/usr/bin/env bash
# Build the optional lab as an independent, portable archive.
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output_directory=${1:-"${repository_root}/dist"}
staging_root=$(mktemp -d)
trap 'rm -rf "${staging_root}"' EXIT

package_root="${staging_root}/oci-opensearch-lab"
mkdir -p "${package_root}" "${output_directory}"
cp -R "${repository_root}/lab/." "${package_root}/"
find "${package_root}" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${package_root}" -type f -name '*.pyc' -delete
rm -rf "${package_root}/.venv" "${package_root}/var"

(
  cd "${package_root}"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

archive="${output_directory}/oci-opensearch-lab.tar.gz"
tar -C "${staging_root}" -czf "${archive}" oci-opensearch-lab
sha256sum "${archive}" > "${archive}.sha256"
printf 'Created %s\n' "${archive}"
