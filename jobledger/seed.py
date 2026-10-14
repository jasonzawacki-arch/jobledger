"""Fictional demo data. Companies are made up; use `python -m jobledger --demo` to load into an empty database."""
from __future__ import annotations

DAY = 86400

DEMO = [
    # company, title, location, pay, stage, days ago applied, closed, follow_up offset (days from now) or None, note
    ("Northwind Analytics", "Data Analyst", "Remote", "$85-100k", 3, 21, None, None, "Panel went well; asked about SQL window functions."),
    ("Fabrikam Logistics", "Operations Analyst", "Boston, MA", "$78-92k", 2, 12, None, None, "Recruiter call booked for Thursday."),
    ("Contoso Health", "Business Systems Analyst", "Hybrid", "$90-105k", 1, 14, None, None, "Applied through the careers page."),
    ("Tailspin Energy", "Reporting Analyst", "Remote", "", 1, 4, None, 3, "Referred by a former coworker."),
    ("Adventure Works", "Product Analyst", "New York, NY", "$95-110k", 0, 1, None, None, ""),
    ("Litware Retail", "BI Developer", "Remote", "$100-120k", 4, 40, None, None, "Verbal offer; waiting on written details."),
    ("Wingtip Finance", "Financial Analyst", "Chicago, IL", "", 1, 30, "ghosted", None, ""),
    ("Proseware Media", "Data Engineer", "Remote", "", 2, 35, "rejected", None, "Went with an internal candidate."),
]

DEMO_RESUME = """- Built weekly sales dashboards in Excel and SQL that cut report preparation from two days to three hours
- Wrote SQL queries and Python scripts to clean and join data from four source systems
- Led a process improvement that reduced order errors by 18 percent across two warehouses
- Trained eleven coworkers on the new reporting tools and wrote the documentation
"""

DEMO_POSTINGS = {
    "Northwind Analytics": """Requirements:
- Strong SQL skills and experience cleaning and joining data
- Experience building dashboards and reports for business teams
- Python scripting for data analysis
- Experience with Tableau or Power BI
- Ability to train coworkers and document processes
""",
}
