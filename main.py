#!/usr/bin/env python3
"""Assign papers to reviewers using Z3.

Rules:
  - Each paper gets ideally 3 reviewers (2..4 acceptable).
  - A reviewer who is in conflict with a paper must not review it.
  - Review workload is balanced across reviewers.

Inputs (tab-separated, downloaded from CMT) live in CMT_files/ by default:
  - Reviewers.txt
  - Papers.txt
  - ReviewerConflicts.txt

Outputs are written to assignment/ by default, two timestamped files per run:
  - assignment_<timestamp>.txt   (human-readable report)
  - assignment_<timestamp>.xml   (machine-readable, CMT import format)

Usage:
  python main.py                       # use the default folders
  python main.py --input DIR --output DIR
"""

import argparse
import datetime
import os
import sys
from xml.sax.saxutils import quoteattr

from z3 import Bool, Int, Solver, If, Sum, sat

HERE = os.path.dirname(os.path.abspath(__file__))

# Default folders (relative to this script). Override on the command line.
DEFAULT_INPUT_DIR = os.path.join(HERE, "CMT_files")
DEFAULT_OUTPUT_DIR = os.path.join(HERE, "assignment")

# Input file names as exported from CMT.
REVIEWERS_FILE = "Reviewers.txt"
PAPERS_FILE = "Papers.txt"
CONFLICTS_FILE = "ReviewerConflicts.txt"

# How many reviewers each paper should get.
TARGET_PER_PAPER = 3   # ideal number of reviewers per paper
MIN_PER_PAPER = 2      # acceptable minimum
MAX_PER_PAPER = 4      # acceptable maximum


def read_rows(path):
    """Read a tab-separated file, skipping blank lines and '#' comments.

    Returns (header_fields, list_of_row_field_lists).
    """
    rows = []
    header = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                # Comment line; treat the first one as the header substitute.
                if header is None:
                    header = [c.strip() for c in line.lstrip("#").split("\t")]
                continue
            fields = line.split("\t")
            if header is None:
                header = [c.strip() for c in fields]
                continue
            rows.append(fields)
    return header, rows


def load_reviewers(path):
    """Return list of (email, full_name)."""
    _, rows = read_rows(path)
    reviewers = []
    for r in rows:
        first = r[0].strip() if len(r) > 0 else ""
        last = r[2].strip() if len(r) > 2 else ""
        email = r[3].strip() if len(r) > 3 else ""
        if not email:
            continue
        name = " ".join(part for part in (first, last) if part)
        reviewers.append((email.lower(), name or email))
    return reviewers


def load_papers(path):
    """Return list of (paper_id, title)."""
    _, rows = read_rows(path)
    papers = []
    for r in rows:
        pid = r[0].strip() if len(r) > 0 else ""
        title = r[3].strip() if len(r) > 3 else ""
        if not pid:
            continue
        papers.append((pid, title))
    return papers


def load_conflicts(path):
    """Return dict: paper_id -> set of conflicting reviewer emails (lowercased)."""
    _, rows = read_rows(path)
    conflicts = {}
    for r in rows:
        if len(r) < 2:
            continue
        pid = r[0].strip()
        email = r[1].strip().lower()
        if not pid or not email:
            continue
        conflicts.setdefault(pid, set()).add(email)
    return conflicts


def solve(reviewers, papers, conflicts):
    """Solve with a plain Solver, optimizing balance by incremental tightening.

    Z3's Optimize engine is very slow on this MaxSMT shape, while plain
    satisfiability is instant. So we optimize manually with push/pop:
      1. minimize the maximum per-reviewer load, then
      2. maximize the minimum per-reviewer load.
    """
    emails = [e for e, _ in reviewers]
    s = Solver()

    # x[(pid, email)] : reviewer assigned to paper?
    x = {}
    for pid, _ in papers:
        for email in emails:
            x[(pid, email)] = Bool(f"x_{pid}_{email}")

    total_target = 0
    for pid, _ in papers:
        conf = conflicts.get(pid, set())
        # Conflict constraints: conflicting reviewer cannot be assigned.
        for email in emails:
            if email in conf:
                s.add(x[(pid, email)] == False)  # noqa: E712

        # Reviewers-per-paper count: aim for TARGET, but never more than the
        # number of non-conflicting reviewers available, and never below MIN.
        count = Sum([If(x[(pid, email)], 1, 0) for email in emails])
        available = sum(1 for email in emails if email not in conf)
        target = min(TARGET_PER_PAPER, available)
        if target < MIN_PER_PAPER:
            # Too many conflicts: take whatever reviewers remain.
            s.add(count == available)
            total_target += available
        else:
            s.add(count == target)
            total_target += target

    # Per-reviewer load expressions.
    loads = {}
    for email in emails:
        loads[email] = Int(f"load_{email}")
        s.add(loads[email] == Sum([If(x[(pid, email)], 1, 0) for pid, _ in papers]))

    n = len(emails)

    # Step 1: minimize the maximum load. The theoretical floor is
    # ceil(total / n); conflicts may push it higher, so search upward.
    lower = -(-total_target // n)  # ceil division
    best_cap = None
    for cap in range(lower, len(papers) + 1):
        s.push()
        for email in emails:
            s.add(loads[email] <= cap)
        if s.check() == sat:
            best_cap = cap
            break
        s.pop()
    if best_cap is None:
        print("ERROR: constraints are unsatisfiable.", file=sys.stderr)
        sys.exit(1)
    # Keep the winning max-load constraint on the stack.

    # Step 2: maximize the minimum load (search downward from best_cap).
    model = s.model()  # fallback model that already satisfies the max cap
    for floor_val in range(best_cap, -1, -1):
        s.push()
        for email in emails:
            s.add(loads[email] >= floor_val)
        if s.check() == sat:
            model = s.model()
            break
        s.pop()

    assignment = {}  # pid -> list of emails
    for pid, _ in papers:
        assigned = [email for email in emails if model.evaluate(x[(pid, email)])]
        assignment[pid] = assigned

    load_result = {email: model.evaluate(loads[email]).as_long() for email in emails}
    return assignment, load_result


def build_human_report(reviewers, papers, conflicts, assignment, load_result, stamp):
    """Return the human-readable report as a string."""
    name_of = {e: n for e, n in reviewers}
    lines = []
    lines.append("=" * 70)
    lines.append("PAPER -> REVIEWER ASSIGNMENTS")
    lines.append(f"Generated: {stamp}")
    lines.append("=" * 70)
    lines.append("")

    for pid, title in papers:
        assigned = assignment[pid]
        lines.append(f"Paper {pid}: {title}")
        lines.append(f"  Reviewers ({len(assigned)}):")
        for email in assigned:
            lines.append(f"    - {name_of.get(email, email)} <{email}>")
        conf = conflicts.get(pid, set())
        if conf:
            conf_names = ", ".join(sorted(name_of.get(e, e) for e in conf))
            lines.append(f"  (conflicts avoided: {conf_names})")
        lines.append("")

    lines.extend(build_workload_section(reviewers, papers, assignment, load_result))
    return "\n".join(lines)


def build_workload_section(reviewers, papers, assignment, load_result):
    """Return the REVIEWER WORKLOAD section as a list of lines.

    reviewers   -> list of (email, name); load_result -> dict email -> count.
    Shared by main.py (initial report) and assignment_sync.py (refresh).
    """
    lines = []
    lines.append("=" * 70)
    lines.append("REVIEWER WORKLOAD")
    lines.append("=" * 70)
    lines.append("")
    # Sort by load desc, then name.
    for email, name in sorted(reviewers, key=lambda rn: (-load_result[rn[0]], rn[1])):
        papers_for = [pid for pid, _ in papers if email in assignment[pid]]
        lines.append(f"  {name} <{email}>: {load_result[email]} paper(s)  "
                     f"[{', '.join(papers_for) if papers_for else '-'}]")
    lines.append("")
    total = sum(load_result.values())
    lines.append(f"Total assignments: {total}")
    lines.append(f"Papers: {len(papers)}   Reviewers: {len(reviewers)}")
    loads = list(load_result.values()) or [0]
    lines.append(f"Load min/max: {min(loads)}/{max(loads)}")
    lines.append("")
    return lines


def build_xml(papers, assignment):
    """Return the CMT-import XML as a string (matches AssignmentsTemplate.xml)."""
    lines = ["<assignments>"]
    for pid, _ in papers:
        lines.append(f"  <submission submissionId={quoteattr(pid)}>")
        for email in assignment[pid]:
            lines.append(f"    <user email={quoteattr(email)} />")
        lines.append("  </submission>")
    lines.append("</assignments>")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Assign papers to reviewers with Z3 (conflict-free, balanced).")
    parser.add_argument("--input", default=DEFAULT_INPUT_DIR,
                        help="folder with the CMT .txt files "
                             f"(default: {os.path.relpath(DEFAULT_INPUT_DIR, HERE)}/)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help="folder to write the timestamped results into "
                             f"(default: {os.path.relpath(DEFAULT_OUTPUT_DIR, HERE)}/)")
    args = parser.parse_args()

    reviewers_path = os.path.join(args.input, REVIEWERS_FILE)
    papers_path = os.path.join(args.input, PAPERS_FILE)
    conflicts_path = os.path.join(args.input, CONFLICTS_FILE)

    for path in (reviewers_path, papers_path, conflicts_path):
        if not os.path.exists(path):
            print(f"ERROR: missing input file: {path}\n"
                  f"Download the CMT files into '{args.input}/' "
                  f"(see README.md).", file=sys.stderr)
            sys.exit(1)

    reviewers = load_reviewers(reviewers_path)
    papers = load_papers(papers_path)
    conflicts = load_conflicts(conflicts_path)

    assignment, load_result = solve(reviewers, papers, conflicts)

    os.makedirs(args.output, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    report = build_human_report(reviewers, papers, conflicts,
                                assignment, load_result, stamp)
    xml = build_xml(papers, assignment)

    txt_path = os.path.join(args.output, f"assignment_{stamp}.txt")
    xml_path = os.path.join(args.output, f"assignment_{stamp}.xml")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml)

    print(report)
    print(f"Wrote human-readable report: {txt_path}")
    print(f"Wrote machine-readable XML : {xml_path}")


if __name__ == "__main__":
    main()
