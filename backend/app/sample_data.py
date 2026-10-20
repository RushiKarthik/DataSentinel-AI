"""
Sample dataset generator for DataSentinel AI.

This creates a CSV with DELIBERATELY KNOWN ISSUES for testing and demo.
Each issue is documented so tests can verify exact counts.

KNOWN ISSUES IN THE SAMPLE DATA:
- 3 rows with missing 'name' values
- 5 rows with missing 'email' values
- 2 rows with missing 'salary' values
- 4 rows with missing 'department' values
- 2 exact duplicate rows (rows for employee_id 101, duplicated)
- 3 duplicate employee_ids (101 appears 3 times, 102 appears 2 times)
- 2 invalid email formats
- 1 invalid date format
- 2 salary outliers (one very high, one negative)
- 1 negative salary value
- Total rows: 30
- Total columns: 7
"""

import pandas as pd
import numpy as np
from io import StringIO


SAMPLE_CSV_CONTENT = """employee_id,name,email,department,hire_date,salary,status
101,Alice Johnson,alice@example.com,Engineering,2022-01-15,85000,Active
102,Bob Smith,bob@example.com,Marketing,2022-03-20,72000,Active
103,Carol Williams,carol@example.com,Engineering,2021-11-10,92000,Active
104,David Brown,david@example.com,Sales,2023-01-05,68000,Active
105,,eve@example.com,Marketing,2022-06-15,71000,Active
106,Frank Miller,frank@example.com,Engineering,2022-09-01,88000,Active
107,Grace Lee,grace@example.com,HR,2021-07-20,76000,Active
108,Henry Wilson,henry@example.com,Sales,2023-04-10,65000,Active
109,,irene@example.com,Engineering,2022-12-01,95000,Active
110,Jack Davis,jack@example.com,Marketing,2023-02-14,69000,Inactive
111,Kate Thomas,kate@example.com,,2022-05-30,78000,Active
112,Leo Martinez,,Sales,2022-08-15,67000,Active
113,Mia Anderson,mia@example.com,Engineering,2021-09-25,91000,Active
114,Noah Garcia,noah@example.com,,2023-03-01,73000,Active
115,Olivia Robinson,invalid-email,HR,2022-11-11,77000,Active
116,Peter Clark,peter@example.com,Marketing,2023-05-20,70000,Active
117,Quinn Lewis,quinn@example.com,Engineering,not-a-date,89000,Active
118,Rachel Hall,,Sales,2022-04-18,66000,Active
119,Sam Young,sam@example.com,,2023-06-01,74000,Active
120,Tina King,bad.email.format,Engineering,2022-10-05,350000,Active
121,Uma Wright,uma@example.com,Marketing,2023-07-15,71000,Active
122,,vince@example.com,HR,2022-02-28,75000,Active
123,Wendy Scott,wendy@example.com,Sales,2023-08-10,-5000,Active
124,Xavier Adams,,Engineering,2022-07-22,87000,Active
125,Yara Baker,yara@example.com,Marketing,2023-01-30,72000,Active
101,Alice Johnson,alice@example.com,Engineering,2022-01-15,85000,Active
102,Bob Smith,bob@example.com,Marketing,2022-03-20,72000,Active
126,Zane Cooper,,Sales,2023-09-05,64000,Active
127,Amy Foster,,,2022-08-30,,Active
128,Brian Reed,brian@example.com,Engineering,2023-10-01,,Active
"""


def get_sample_dataframe() -> pd.DataFrame:
    """
    Load the sample dataset as a pandas DataFrame.

    Returns:
        A DataFrame with 30 rows and 7 columns containing known issues.
    """
    return pd.read_csv(StringIO(SAMPLE_CSV_CONTENT.strip()))


def get_sample_csv_bytes() -> bytes:
    """Return the sample CSV as bytes for download."""
    return SAMPLE_CSV_CONTENT.strip().encode('utf-8')


# Document expected counts for testing
EXPECTED_ISSUES = {
    "total_rows": 30,
    "total_columns": 7,
    "missing_name": 3,       # rows 105, 109, 122
    "missing_email": 5,      # rows 112, 118, 124, 126, plus count
    "missing_salary": 2,     # rows 127, 128
    "missing_department": 4, # rows 111, 114, 119, 127
    "duplicate_rows": 4,     # rows for 101 and 102 (original + duplicate = 4 rows in dup groups)
    "invalid_emails": 2,     # rows 115, 120
    "invalid_dates": 1,      # row 117
    "salary_outliers": 2,    # row 120 (350000) and row 123 (-5000) — depends on IQR
}
