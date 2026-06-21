# Reviewer Auto-Assignment Tool

A small Python tool that assigns conference papers to reviewers using the
[Z3](https://github.com/Z3Prover/z3) constraint solver. It guarantees that no
reviewer is assigned a paper they have a conflict with, and balances the review
workload as evenly as possible across reviewers.

## What it does

- Reads reviewers, papers, and conflicts from three tab-separated files exported
  from CMT.
- Assigns **ideally 3 reviewers per paper** (2–4 acceptable if conflicts leave
  fewer reviewers available).
- Enforces, as a hard constraint, that a **conflicting reviewer never reviews a
  paper**.
- **Balances workload** across reviewers: first it minimizes the maximum number
  of papers any one reviewer gets, then it maximizes the minimum — squeezing
  everyone toward an even split.
- Writes two timestamped result files per run: a **human-readable** report and a
  **machine-readable XML** file you can import back into CMT.

## Folder layout

```
.
├── main.py              # the tool
├── CMT_files/           # <-- you put the three CMT exports here
│   ├── Reviewers.txt
│   ├── Papers.txt
│   └── ReviewerConflicts.txt
└── assignment/          # <-- results are written here
    ├── AssignmentsTemplate.xml      # reference for the CMT import format
    ├── assignment_<timestamp>.txt   # produced each run (human-readable)
    └── assignment_<timestamp>.xml   # produced each run (machine-readable)
```

## Step 1 — Provide the input files

Download these three files from the **CMT** (Microsoft Conference Management
Toolkit) conference site and place them in the **`CMT_files/`** folder. They are
tab-separated; lines starting with `#` and blank lines are ignored.

### `Reviewers.txt`
The reviewer list export. The tool reads these columns:

| Column | Field |
| ------ | ----- |
| 1 | First Name |
| 3 | Last Name |
| 4 | Email (used as the reviewer's unique id) |

Other columns are ignored. The first non-comment row is treated as a header.

### `Papers.txt`
The submissions export. The tool reads these columns:

| Column | Field |
| ------ | ----- |
| 1 | Paper ID |
| 4 | Paper Title |

Other columns are ignored. The first non-comment row is treated as a header.

### `ReviewerConflicts.txt`
The conflicts export — one conflict per row, two columns:

```
# Paper ID    Reviewer Email
4             carol@example.com
4             dave@example.com
...
```

Each row says "this reviewer is in conflict with this paper" and therefore must
not be assigned to it. Papers with no conflicts simply have no rows here.

## Step 2 — Set up Z3 (once)

The system Python on macOS is "externally managed" (PEP 668), so install Z3 in a
local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install z3-solver
```

## Step 3 — Run

```bash
.venv/bin/python main.py
```

By default this reads from `CMT_files/` and writes to `assignment/`. To use
different folders:

```bash
.venv/bin/python main.py --input path/to/cmt --output path/to/results
```

## Where to find the results

Each run writes **two timestamped files** into the **`assignment/`** folder
(the timestamp is `YYYYMMDD-HHMMSS`, so runs never overwrite each other):

- **`assignment_<timestamp>.txt`** — human-readable. Two sections:
  1. *Paper → Reviewer assignments* — for each paper, the assigned reviewers
     (names + emails) and, for transparency, the conflicting reviewers avoided.
  2. *Reviewer workload* — how many papers each reviewer got and which ones,
     plus totals and the min/max load spread.

  ```
  Paper 7: An Example Paper Title
    Reviewers (3):
      - Alice Anderson <alice@example.com>
      - Bob Brown <bob@example.com>
      - Frank Foster <frank@example.com>
    (conflicts avoided: Grace Green, Heidi Hughes, Ivan Ivanov)
  ...
  Total assignments: 33
  Papers: 11   Reviewers: 13
  Load min/max: 2/3
  ```

- **`assignment_<timestamp>.xml`** — machine-readable, in the CMT import format
  (matches `assignment/AssignmentsTemplate.xml`). Upload this to CMT to apply the
  assignments:

  ```xml
  <assignments>
    <submission submissionId="4">
      <user email="alice@example.com" />
      <user email="bob@example.com" />
      <user email="carol@example.com" />
    </submission>
    ...
  </assignments>
  ```

The full report is also printed to the terminal, ending with the two output
paths.

## Tuning

The number of reviewers per paper is controlled by constants near the top of
`main.py`:

```python
TARGET_PER_PAPER = 3   # ideal number of reviewers per paper
MIN_PER_PAPER = 2      # acceptable minimum
MAX_PER_PAPER = 4      # acceptable maximum
```

Change `TARGET_PER_PAPER` to assign a different number of reviewers per paper.
If a paper has so many conflicts that fewer than `MIN_PER_PAPER` reviewers
remain, the tool assigns all remaining non-conflicting reviewers.

## How the solving works

The assignment is modeled as boolean variables `x[paper, reviewer]`. Constraints:

- Conflicting `(paper, reviewer)` pairs are forced to `False`.
- Each paper's reviewer count is pinned to its target.
- Each reviewer's load is the sum of their assignments.

Z3's built-in `Optimize` (MaxSMT) engine turned out to be extremely slow on this
problem shape, while plain satisfiability solves instantly. So balancing is done
manually with a plain `Solver` and incremental `push`/`pop`: search upward for
the smallest feasible maximum load, then downward for the largest feasible
minimum load. This yields the same optimal balance and runs in well under a
second.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'z3'`** — point at the venv: run
  `.venv/bin/python main.py`, or reinstall with `.venv/bin/pip install z3-solver`.
- **`ERROR: missing input file: ...`** — the three CMT exports aren't in the
  `CMT_files/` folder (or the `--input` folder). See Step 1.
- **`ERROR: constraints are unsatisfiable.`** — a paper cannot be assigned even
  its minimum reviewers given the conflicts. Check `ReviewerConflicts.txt` for a
  paper that conflicts with (nearly) all reviewers.
