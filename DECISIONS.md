# Design Decisions

1. **Indexes:** I use a unique index on `emp_code` to prevent duplicate employees and a unique compound index on `emp_code` and `date` to prevent duplicate daily attendance records. Additional indexes support attendance filtering and analytics. I would avoid adding indexes without a query-performance benefit because indexes increase storage and write costs.

2. **Punch-in race:** Two identical requests may arrive simultaneously. Both can attempt an insert, but the unique compound index allows only one record for the same employee and attendance date. The other request receives a conflict response.

3. **Ties:** Employees with equal late-minute totals should share the same rank. Standard competition ranking skips the next rank after a tie. The limit is applied after ranking so tied employees at the cutoff can be included.

4. **Headcount:** The department summary starts from employees who joined by the reporting period's end and includes employees even when they have no attendance logs. Attendance statistics are calculated from matching records.

5. **Scaling:** For 100 times more data, I would examine query execution statistics, add only indexes justified by common queries, and consider pre-aggregating frequently requested analytics.
