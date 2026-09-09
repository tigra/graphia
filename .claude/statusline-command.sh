#!/bin/bash
# Claude Code status line: model name, current git branch, context usage bar.
# Readable ANSI colors for dark themes (model name intentionally dimmed, dividers gray).

input=$(cat)

model=$(printf '%s' "$input" | jq -r '.model.display_name // "unknown"')
cwd=$(printf '%s' "$input" | jq -r '.workspace.current_dir // .cwd // empty')
used=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // empty')

RESET="\033[0m"
C_MODEL="\033[2;95m"   # dimmed pink — model name
C_BRANCH="\033[36m"    # cyan — git branch (one notch below bright)
C_GREEN="\033[32m"     # green — low context usage
C_YELLOW="\033[33m"    # yellow — mid context usage
C_RED="\033[31m"       # red — high context usage
C_GRAY="\033[90m"      # gray — dividers and percentage label

# Current git branch (skip optional locks: safe to call while other git ops run)
branch=""
if [ -n "$cwd" ] && git -C "$cwd" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null)
fi

# Context usage progress bar (10 slots)
bar=""
pct_label=""
if [ -n "$used" ]; then
  slots=10
  filled=$(awk -v u="$used" -v s="$slots" 'BEGIN { f = int((u * s / 100) + 0.5); if (f > s) f = s; if (f < 0) f = 0; print f }')
  empty=$((slots - filled))

  if awk -v u="$used" 'BEGIN { exit !(u >= 80) }'; then
    bar_color="$C_RED"
  elif awk -v u="$used" 'BEGIN { exit !(u >= 50) }'; then
    bar_color="$C_YELLOW"
  else
    bar_color="$C_GREEN"
  fi

  # Fill with a loop, not tr: GNU tr is byte-oriented and truncates multibyte
  # glyphs in SET2 (BSD tr handles them), so `tr ' ' '█'` mojibakes on Linux.
  filled_str=""
  for ((i = 0; i < filled; i++)); do filled_str+="█"; done
  empty_str=""
  for ((i = 0; i < empty; i++)); do empty_str+="░"; done
  bar="${bar_color}${filled_str}${empty_str}${RESET}"
  pct_label=$(awk -v u="$used" 'BEGIN { printf "%.0f%%", u }')
fi

parts=()
[ -n "$bar" ] && parts+=("${bar} ${C_GRAY}${pct_label}${RESET}")
parts+=("${C_MODEL}${model}${RESET}")
[ -n "$branch" ] && parts+=("${C_BRANCH}${branch}${RESET}")

output=""
for p in "${parts[@]}"; do
  if [ -z "$output" ]; then
    output="$p"
  else
    output="${output} ${C_GRAY}|${RESET} ${p}"
  fi
done

printf '%b\n' "$output"
