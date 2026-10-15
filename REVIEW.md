# Starter Code Review

| # | Where | Defect | How to notice | Fix |
|---|---|---|---|---|
| 1 | `health()` | Returns success without checking MongoDB availability. | Stop or disconnect MongoDB and request `/health`. | Check database connectivity and return 503 when unavailable. |
| 2 | `create_employee()` | Checks duplicates before insertion, allowing concurrent duplicate requests. | Send simultaneous requests with the same employee code. | Enforce uniqueness with a MongoDB unique index and handle duplicate-key errors. |
| 3 | `create_employee()` | Does not validate employee code format, shift values, or joined date adequately. | Submit malformed employee fields. | Add input validation and reject invalid values with 422. |
| 4 | `create_employee()` | Uses a naive local `datetime.now()` for creation time. | Run under different system timezones. | Use consistent UTC timestamps. |
| 5 | `list_employees()` | Calculates the page offset as `page * page_size`, skipping the first page's records. | Request page 1 with page size 20. | Use `(page - 1) * page_size`. |
| 6 | `list_employees()` | Counts all employees instead of applying the department filter to the total. | Request a department with fewer employees than the full collection. | Count using the same filter as the listing query. |
| 7 | `list_employees()` | Does not sort results or enforce pagination bounds. | Request different pages or invalid page sizes. | Sort by employee code and validate page and page size. |
| 8 | `punch_in()` | Does not handle an unknown employee before accessing shift fields. | Punch in using a nonexistent employee code. | Return 404 when the employee does not exist. |
| 9 | `punch_in()` | Uses local-time conversions rather than consistent IST handling and does not correctly normalize timestamps. | Submit timestamps near midnight or with fractional seconds. | Normalize instants to IST for attendance dates and truncate to whole seconds. |
| 10 | `punch_in()` | The duplicate check followed by insertion is not race-safe. | Send concurrent punch-in requests for the same employee and date. | Use a unique compound index on employee code and attendance date; map duplicate-key errors to 409. |
| 11 | `punch_in()` | Does not enforce all permitted statuses or validate the timestamp and employee code. | Submit an invalid status or timestamp. | Validate request fields and return 422 for invalid input. |
| 12 | `compute_late_minutes()` | Assumes the shift starts on the punch-in calendar date, which fails for overnight shifts. | Test an overnight shift around midnight. | Determine the correct shift start date before calculating lateness. |
| 13 | `compute_late_minutes()` | Does not explicitly implement whole-second normalization and the required late-minute rounding rules. | Test timestamps around the ten-minute threshold. | Apply the specified threshold and floor-minute calculation consistently. |
| 14 | `compute_work_hours()` | Python `round()` does not guarantee the required decimal half-up rounding. | Test values exactly halfway between two hundredths. | Use decimal half-up rounding on elapsed seconds. |
| 15 | `compute_overtime()` | Assumes shift end is on the attendance date and omits the required 30-minute minimum. | Test overnight shifts and overtime below 30 minutes. | Calculate the correct shift end, including overnight shifts, and apply the threshold. |
| 16 | `list_attendance()` | Loads all matching attendance documents into Python and sorts in memory. | Test with a large attendance collection. | Filter, sort, count, and paginate in MongoDB using suitable indexes. |
| 17 | `list_attendance()` | Sorts only by date and does not use the required employee-code tie-breaker. | Create records with the same attendance date. | Sort by date descending and employee code ascending. |
| 18 | `list_attendance()` | Returns an `id` derived from MongoDB `_id`, contrary to the attendance response contract. | Inspect a returned attendance record. | Return the contract fields without exposing or inventing an attendance ID. |
| 19 | `list_attendance()` | Does not validate date ranges or page parameters adequately. | Supply an invalid date range or page size. | Validate dates and pagination; return 422 for invalid inputs. |
| 20 | Application startup | Does not create the required indexes and the remaining contract endpoints are missing. | Start with an empty database and inspect routes and indexes. | Create indexes idempotently at startup and implement all required endpoints. |

## Reviewed and retained

- The default shift values are reasonable defaults, not defects by themselves.
- The ten-minute lateness grace period is intentional; the defect is incorrect handling around the threshold or shift boundary.
- Returning 409 for duplicate attendance is appropriate, provided uniqueness is enforced safely by MongoDB.
