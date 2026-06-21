#!/usr/bin/env python3
"""Sync hand-edited human-readable assignments back into their XML files.

Workflow:
  1. Run main.py to produce assignment_<timestamp>.txt and .xml.
  2. Hand-edit the .txt file (add/remove/replace reviewers) however you like.
  3. Run this script. For each .txt it:
       - refreshes the "REVIEWER WORKLOAD" stats section in the .txt to match
         your edits, and
       - regenerates the matching .xml so the machine-readable file reflects
         your manual edits.

The reviewer assignment lines in the .txt are the source of truth; the workload
section and the .xml are recomputed from them.

Usage:
  python assignment_sync.py                       # sync every .txt in assignment/
  python assignment_sync.py --output DIR          # sync every .txt in DIR
  python assignment_sync.py FILE1.txt FILE2.txt   # sync only these files
"""

import argparse
import glob
import os
import re
import sys

from main import build_xml, build_workload_section, DEFAULT_OUTPUT_DIR

# "Paper 7: Some Title" -> capture the id (first non-space token before ':').
PAPER_RE = re.compile(r"^Paper\s+(\S+):")
# "    - Some Name <email@host>" -> assignment line: capture name and email.
REVIEWER_RE = re.compile(r"^\s+-\s+(.*?)\s*<([^>]+)>")
# "  Some Name <email@host>: 3 paper(s)  [...]" -> workload roster line.
WORKLOAD_LINE_RE = re.compile(r"^\s+(.*?)\s*<([^>]+)>:\s*\d+\s+paper")
# "  Reviewers (3):" -> the per-paper count line, refreshed to match edits.
REVIEWERS_COUNT_RE = re.compile(r"^(\s+Reviewers \()\d+(\):\s*)$")
# Everything from this heading down is the workload summary, not assignments.
WORKLOAD_MARKER = "REVIEWER WORKLOAD"


def parse_report(path):
    """Parse a human-readable report.

    Returns (papers, assignment, names, roster):
      papers     -> list of (paper_id, title) in file order
      assignment -> dict paper_id -> list of reviewer emails
      names      -> dict email -> display name (from both sections)
      roster     -> list of all reviewer emails (assignment + workload), in
                    first-seen order; includes reviewers with 0 papers
    """
    papers = []
    assignment = {}
    names = {}
    roster = []
    current = None
    in_workload = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if WORKLOAD_MARKER in line:
                in_workload = True
                current = None
                continue

            if not in_workload:
                m = PAPER_RE.match(line)
                if m:
                    pid = m.group(1)
                    current = pid
                    papers.append((pid, line.split(":", 1)[1].strip()))
                    assignment[pid] = []
                    continue
                if current is not None:
                    rm = REVIEWER_RE.match(line)
                    if rm:
                        name, email = rm.group(1).strip(), rm.group(2).strip()
                        assignment[current].append(email)
                        if email not in names or not names[email]:
                            names[email] = name
                        if email not in roster:
                            roster.append(email)
            else:
                wm = WORKLOAD_LINE_RE.match(line)
                if wm:
                    name, email = wm.group(1).strip(), wm.group(2).strip()
                    if email not in names or not names[email]:
                        names[email] = name
                    if email not in roster:
                        roster.append(email)
    return papers, assignment, names, roster


def refresh_workload(txt_path):
    """Rewrite the .txt's workload section from its assignment lines.

    Returns (papers, assignment): the parsed assignments (for XML generation).
    """
    papers, assignment, names, roster = parse_report(txt_path)
    if not papers:
        raise ValueError(f"no paper assignments found in {txt_path}")

    load_result = {
        email: sum(1 for pid, _ in papers if email in assignment[pid])
        for email in roster
    }
    reviewers = [(email, names.get(email) or email) for email in roster]
    workload_lines = build_workload_section(reviewers, papers, assignment, load_result)

    # Splice the freshly built workload section onto the assignment portion.
    with open(txt_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    try:
        marker_idx = next(i for i, ln in enumerate(lines) if WORKLOAD_MARKER in ln)
        # The "=" separator line directly precedes the marker; cut from there.
        cut = marker_idx - 1
        while cut > 0 and not lines[cut].startswith("="):
            cut -= 1
        head = lines[:cut]
    except StopIteration:
        # No existing workload section; keep the body and append a fresh one.
        head = lines
        while head and head[-1].strip() == "":
            head.pop()
        head.append("")

    # Refresh each paper's "Reviewers (N):" count to match the edited block.
    cur = None
    for i, ln in enumerate(head):
        pm = PAPER_RE.match(ln)
        if pm:
            cur = pm.group(1)
            continue
        cm = REVIEWERS_COUNT_RE.match(ln)
        if cm and cur in assignment:
            head[i] = f"{cm.group(1)}{len(assignment[cur])}{cm.group(2)}"

    new_text = "\n".join(head + workload_lines)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(new_text)

    return papers, assignment


def sync_file(txt_path):
    """Refresh the .txt workload section and regenerate the .xml.

    Returns (xml_path, n_papers).
    """
    papers, assignment = refresh_workload(txt_path)
    xml = build_xml(papers, assignment)
    xml_path = os.path.splitext(txt_path)[0] + ".xml"
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return xml_path, len(papers)


def main():
    parser = argparse.ArgumentParser(
        description="Sync hand-edited assignment .txt files into their .xml files.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help="folder holding assignment_*.txt files "
                             f"(default: {os.path.relpath(DEFAULT_OUTPUT_DIR)}/)")
    parser.add_argument("files", nargs="*",
                        help="specific .txt files to sync "
                             "(default: all assignment_*.txt in --output)")
    args = parser.parse_args()

    if args.files:
        txts = args.files
    else:
        txts = sorted(glob.glob(os.path.join(args.output, "assignment_*.txt")))

    if not txts:
        print(f"No assignment_*.txt files found in '{args.output}/'. "
              f"Run main.py first.", file=sys.stderr)
        sys.exit(1)

    for txt in txts:
        if not os.path.exists(txt):
            print(f"ERROR: file not found: {txt}", file=sys.stderr)
            sys.exit(1)
        xml_path, n = sync_file(txt)
        print(f"synced {txt} -> {xml_path}  ({n} papers)")


if __name__ == "__main__":
    main()
