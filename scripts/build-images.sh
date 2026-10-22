#!/usr/bin/env bash
# Build the four production images (and push them with --push). Used by CI (build only), by the
# release workflow (build + push on a v* tag) and by scripts/prod-smoke.sh (local dry run).
#
#   scripts/build-images.sh --tag 0.1.0 [--tag sha-abc1234] [--push] [--source URL] [--list]
#
# --source adds the OCI "source" label, which links each GHCR package to its repository.
# --list only prints the image names it would build.
set -euo pipefail

PREFIX="ghcr.io/kit2code"
# name, Dockerfile, build target ("-" for the last stage)
IMAGES=(
  "grader-web web/Dockerfile prod"
  "grader-worker judge/worker/Dockerfile -"
  "grader-edge deploy/edge/Dockerfile -"
  "grader-ops deploy/ops/Dockerfile -"
)

tags=()
push=0
list=0
source_url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) tags+=("$2"); shift 2 ;;
    --push) push=1; shift ;;
    --list) list=1; shift ;;
    --source) source_url="$2"; shift 2 ;;
    *) echo "build-images: unknown option $1" >&2; exit 2 ;;
  esac
done
if [ ${#tags[@]} -eq 0 ]; then
  echo "build-images: give at least one --tag" >&2
  exit 2
fi
for tag in "${tags[@]}"; do
  if ! [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "build-images: not a valid image tag: $tag" >&2
    exit 2
  fi
done

cd "$(dirname "$0")/.."
for entry in "${IMAGES[@]}"; do
  read -r name dockerfile target <<<"$entry"
  if [ "$list" = 1 ]; then
    for tag in "${tags[@]}"; do echo "$PREFIX/$name:$tag"; done
    continue
  fi
  args=(build -f "$dockerfile")
  [ "$target" != "-" ] && args+=(--target "$target")
  [ -n "$source_url" ] && args+=(--label "org.opencontainers.image.source=$source_url")
  for tag in "${tags[@]}"; do args+=(-t "$PREFIX/$name:$tag"); done
  echo "build-images: $name"
  docker "${args[@]}" .
  if [ "$push" = 1 ]; then
    for tag in "${tags[@]}"; do docker push "$PREFIX/$name:$tag"; done
  fi
done
