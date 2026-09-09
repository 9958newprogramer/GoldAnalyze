#!/usr/bin/env bash

set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

remote="${GITHUB_REMOTE:-origin}"
expected_repo="9958newprogramer/GoldAnalyze"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: $repo_dir is not a Git repository." >&2
  exit 1
fi

if ! remote_url="$(git remote get-url "$remote" 2>/dev/null)"; then
  echo "Error: Git remote '$remote' is not configured." >&2
  exit 1
fi

case "$remote_url" in
  *github.com:*"$expected_repo"*|*github.com/*"$expected_repo"*) ;;
  *)
    echo "Error: '$remote' points to '$remote_url', not '$expected_repo'." >&2
    exit 1
    ;;
esac

branch="$(git symbolic-ref --quiet --short HEAD)" || {
  echo "Error: detached HEAD; switch to a branch before syncing." >&2
  exit 1
}

git add -A

if git diff --cached --quiet; then
  echo "No local changes to commit."
else
  commit_message="${1:-sync: $(date '+%Y-%m-%d %H:%M:%S %z')}"
  git commit -m "$commit_message"
fi

if git rev-parse --verify --quiet "refs/remotes/$remote/$branch" >/dev/null; then
  git pull --rebase "$remote" "$branch"
fi

git push --set-upstream "$remote" "$branch"

echo "Synced $branch to $remote_url"

