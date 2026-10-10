# DECISIONS.md

1. **Indexes.**
   - `employees` (unique `emp_code`): Guarantees unique employee creation.
   - `employees` (`department`): Speeds up filtering for lists and analytics.
   - `attendance_logs` (unique `emp_code, date`): Guarantees a single record per employee per day and accelerates PATCH/lookups.
   - `attendance_logs` (`date`): Speeds up the global attendance list.
   - Rejected: Indexing `status` because its low cardinality makes a scan just as efficient.

2. **Punch-in race.**
   Both requests evaluate to the same `date` and attempt an `insert_one()`. The first succeeds. The second hits the unique compound index on `(emp_code, date)` and throws a `DuplicateKeyError`. The code catches this and returns a 409 Conflict, ensuring exactly one record is created.

3. **Ties.**
   The leaderboard uses MongoDB's `$rank` window function. Ties receive the identical rank number. The query applies the `$match` filter `rank <= limit`. If multiple people tie precisely at the cutoff limit (e.g., rank 10), the response will include all of them, exceeding the limit size by design.

4. **Headcount.**
   The aggregation pipeline starts at the `employees` collection (filtered by `joined_on` bounds) and performs a `$lookup` against `attendance_logs`. Crucially, it uses `$unwind` with `preserveNullAndEmptyArrays: True`. This ensures employees with zero logs are retained in the pipeline to be properly tallied in the final `$group` headcount.

5. **One thing you would change.**
   If scaling 100x, I would stop running heavy analytical aggregations on-the-fly against the raw operational `attendance_logs` collection. Instead, I would introduce a background cron job or event stream to incrementally materialize daily/monthly stats into a pre-computed analytics collection.
