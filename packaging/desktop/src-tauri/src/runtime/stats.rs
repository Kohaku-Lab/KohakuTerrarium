//! Memory used by the backend process and everything it started.

use std::collections::{HashMap, HashSet};

use sysinfo::{ProcessesToUpdate, System};

/// One process as the system reports it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Row {
    pub pid: u32,
    pub parent: Option<u32>,
    pub memory: u64,
}

/// Bytes used by `root` and all of its descendants, and how many processes that is.
pub fn tree_usage(root: u32, rows: &[Row]) -> Option<(u64, usize)> {
    if !rows.iter().any(|row| row.pid == root) {
        return None;
    }
    let mut children: HashMap<u32, Vec<&Row>> = HashMap::new();
    for row in rows {
        if let Some(parent) = row.parent.filter(|parent| *parent != row.pid) {
            children.entry(parent).or_default().push(row);
        }
    }
    let by_pid: HashMap<u32, &Row> = rows.iter().map(|row| (row.pid, row)).collect();
    let mut seen: HashSet<u32> = HashSet::new();
    let mut pending = vec![root];
    let mut bytes = 0u64;
    while let Some(pid) = pending.pop() {
        if !seen.insert(pid) {
            continue;
        }
        if let Some(row) = by_pid.get(&pid) {
            bytes = bytes.saturating_add(row.memory);
        }
        for child in children.get(&pid).into_iter().flatten() {
            pending.push(child.pid);
        }
    }
    Some((bytes, seen.len()))
}

/// Memory of the process tree under `root` right now, or `None` when it is not running.
pub fn collect(root: u32) -> Option<(u64, usize)> {
    let mut system = System::new().ok()?;
    system.refresh_processes(ProcessesToUpdate::All, true);
    let rows: Vec<Row> = system
        .processes()
        .iter()
        .map(|(pid, process)| Row {
            pid: pid.as_u32(),
            parent: process.parent().map(|parent| parent.as_u32()),
            memory: process.memory(),
        })
        .collect();
    tree_usage(root, &rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(pid: u32, parent: Option<u32>, memory: u64) -> Row {
        Row {
            pid,
            parent,
            memory,
        }
    }

    #[test]
    fn the_tree_sums_the_root_and_every_descendant_but_nothing_else() {
        let rows = [
            row(1, None, 100),
            row(10, Some(1), 10),
            row(11, Some(10), 1),
            row(12, Some(10), 2),
            row(20, Some(2), 1000),
            row(2, None, 5000),
        ];
        assert_eq!(tree_usage(10, &rows), Some((13, 3)));
        assert_eq!(tree_usage(1, &rows), Some((113, 4)));
        assert_eq!(tree_usage(11, &rows), Some((1, 1)));
    }

    #[test]
    fn a_missing_root_has_no_usage() {
        assert_eq!(tree_usage(99, &[row(1, None, 1)]), None);
        assert_eq!(tree_usage(1, &[]), None);
    }

    #[test]
    fn a_process_that_claims_itself_or_a_loop_is_counted_once() {
        let rows = [row(1, Some(1), 7), row(2, Some(3), 1), row(3, Some(2), 1)];
        assert_eq!(tree_usage(1, &rows), Some((7, 1)));
        assert_eq!(tree_usage(2, &rows), Some((2, 2)));
    }

    #[test]
    fn the_running_test_process_reports_its_own_memory() {
        let (bytes, count) = collect(std::process::id()).expect("this process exists");
        assert!(bytes > 0);
        assert!(count >= 1);
        assert_eq!(collect(u32::MAX - 1), None);
    }
}
