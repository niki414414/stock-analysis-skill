#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: export_simple_word.sh SOURCE_TEXT OUTPUT_DOCX" >&2
  exit 2
fi

source_text=$1
output_docx=$2

if [ ! -f "$source_text" ]; then
  echo "source file not found: $source_text" >&2
  exit 2
fi

mkdir -p "$(dirname "$output_docx")"
/usr/bin/textutil -convert docx -format txt -inputencoding UTF-8 \
  -font "PingFang SC" -fontsize 11 -output "$output_docx" "$source_text"

test -s "$output_docx"
/usr/bin/unzip -tqq "$output_docx"
/usr/bin/textutil -info "$output_docx"
