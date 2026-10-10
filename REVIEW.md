# Review of Starter Code

## Defects Found and Fixed

1. **`health()` endpoint (Line 72)**
   * **What is wrong:** It hardcodes a `{"status": "ok"}` response without checking the MongoDB connection.
   * **How you'd notice:** If the database goes down, the health check still returns HTTP 200.
   * **How I fixed it:** Added `db.command("ping")` wrapped in a `try-except` block to return a 503 if the database is unreachable.

2. **`create_employee()` race condition (Line 79)**
   * **What is wrong:** Uses `find_one()` followed by `insert_one()`. This is not atomic.
   * **How you'd notice:** If two requests to create the same `emp_code` arrive simultaneously, both pass the `find_one()` check and insert duplicates.
   * **How I fixed it:** Created a unique index on `emp_code` at app startup, removed the `find_one()` check, and caught `pymongo.errors.DuplicateKeyError` to raise a 409 Conflict.

3. **`create_employee()` naive datetime (Line 82)**
   * **What is wrong:** `datetime.now()` creates a naive local time object. MongoDB stores BSON dates in UTC.
   * **How you'd notice:** Fetching the document shows the local timezone's time mistakenly saved as UTC, shifting the timestamp by 5.5 hours.
   * **How I fixed it:** Changed it to `datetime.now(timezone.utc)`.

4. **`list_employees()` pagination bug (Line 93)**
   * **What is wrong:** Pagination uses `skip = page * page_size`. Since `page` is 1-indexed, page 1 skips the first 20 records.
   * **How you'd notice:** The first 20 employees added would never appear on page 1.
   * **How I fixed it:** Changed the formula to `skip = (page - 1) * page_size`.

5. **`list_employees()` missing sort and total filter (Line 94-95)**
   * **What is wrong:** `total` ignores the department filter. The query does not sort by `emp_code` as required by the rules.
   * **How you'd notice:** Passing a `department` filter still returns the total count of *all* employees. The list order is unpredictable.
   * **How I fixed it:** Updated `count_documents(q)` and appended `.sort("emp_code", 1)` to the cursor.

6. **`punch_in()` 404 missing and naive dates (Line 101)**
   * **What is wrong:** If an `emp_code` doesn't exist, `emp` is `None`, resulting in a 500 error when accessing `emp["shift_start"]`. Also, timestamp logic incorrectly converts Epoch to a naive local datetime instead of UTC, and the attendance date is calculated directly from this naive datetime. 
   * **How you'd notice:** Punching in a fake ID returns a 500 Internal Server Error. Punching in for an overnight shift at 1 AM sets the attendance date to the next day instead of the shift start day.
   * **How I fixed it:** Added a 404 HTTP Exception. Updated timestamp parsing to use `tz=timezone.utc`. Added logic to compute the correct "attendance date" by analyzing if the shift is overnight and the time falls before the shift end.

7. **`punch_in()` race condition (Line 104)**
   * **What is wrong:** Another non-atomic `find_one()` check for existing punch-ins.
   * **How you'd notice:** Double-clicking a punch-in button creates two attendance records for the same day.
   * **How I fixed it:** Created a unique compound index on `(emp_code, date)` and caught `DuplicateKeyError`.

8. **`compute_late_minutes()` grace period & overnight shifts (Line 36-37)**
   * **What is wrong:** Uses `int((...) / 60)` which floors the value. So 10 minutes and 59 seconds returns 10, failing the "strictly more than 10:00" rule. Also breaks for overnight shifts.
   * **How you'd notice:** Punching in at 09:40:59 for a 09:30 shift returns 0 late minutes instead of 10.
   * **How I fixed it:** Changed the logic to compare total elapsed seconds `> 600`. Passed the correct computed shift start datetime.

9. **`compute_work_hours()` rounding (Line 41)**
   * **What is wrong:** Uses Python's built-in `round()`, which applies Banker's rounding (half-to-even) instead of "half-up".
   * **How you'd notice:** 4.125 hours rounds to 4.12 instead of 4.13.
   * **How I fixed it:** Imported the `decimal` module to explicitly apply `ROUND_HALF_UP`.

10. **`compute_overtime()` minimum threshold (Line 47)**
    * **What is wrong:** Computes exact overtime minutes but completely ignores the 30-minute minimum rule (R3).
    * **How you'd notice:** Checking out 15 minutes after shift end gives 15 overtime minutes instead of 0.
    * **How I fixed it:** Added a check: `return minutes if minutes >= 30 else 0`.

11. **`list_attendance()` performance (Line 144)**
    * **What is wrong:** It fetches the entire collection into a Python list, then sorts and slices it in Python.
    * **How you'd notice:** Calling this on 100,000 records takes seconds and spikes RAM usage, violating the "fast at 100k docs" constraint.
    * **How I fixed it:** Implemented pagination and sorting natively in MongoDB using `.sort("date", -1).skip(skip).limit(page_size)`.

## What was examined and found fine
* The basic project structure and endpoints are conceptually correct.
* The separation of helper functions for computing metrics is a good pattern, despite the original logic being flawed.
* PyMongo initialization without `async` is fine here, as standard PyMongo is synchronous and FastAPI runs standard `def` endpoints in thread pools.
