@echo off
cd /d E:\claude\nanobot
git add -A
git commit -m "configurable context compression: context_max_turns, context_trim_batch"
git status --short