"""Canonical list of Virginia Tech academic departments and schools.

Used to constrain the LLM when inferring which department a converted
notes document most likely belongs to. Compiled from the Virginia Tech
college/department listings (vt.edu, eng.vt.edu, science.vt.edu,
liberalarts.vt.edu, Wikipedia college pages), July 2026.
"""

from typing import Final

# Sentinel used when inference fails or no department is a plausible match
UNKNOWN_DEPARTMENT: Final[str] = "Unknown"

VT_DEPARTMENTS: Final[list[str]] = [
    # College of Agriculture and Life Sciences
    "Agricultural and Applied Economics",
    "Agricultural, Leadership, and Community Education",
    "Animal and Poultry Sciences",
    "Biochemistry",
    "Dairy Science",
    "Entomology",
    "Food Science and Technology",
    "Horticulture",
    "Human Nutrition, Foods, and Exercise",
    "Plant and Environmental Sciences",
    # College of Architecture, Arts, and Design
    "Architecture",
    "Design",
    "Performing Arts",
    "Visual Arts",
    # Pamplin College of Business
    "Accounting and Information Systems",
    "Business Information Technology",
    "Finance",
    "Hospitality and Tourism Management",
    "Management",
    "Marketing",
    # College of Engineering
    "Aerospace and Ocean Engineering",
    "Biological Systems Engineering",
    "Biomedical Engineering and Mechanics",
    "Chemical Engineering",
    "Civil and Environmental Engineering",
    "Computer Science",
    "Construction",
    "Electrical and Computer Engineering",
    "Engineering Education",
    "Industrial and Systems Engineering",
    "Materials Science and Engineering",
    "Mechanical Engineering",
    "Mining and Minerals Engineering",
    # College of Liberal Arts and Human Sciences
    "Apparel, Housing, and Resource Management",
    "Communication",
    "Education",
    "English",
    "History",
    "Human Development and Family Science",
    "Modern and Classical Languages and Literatures",
    "Philosophy",
    "Political Science",
    "Public and International Affairs",
    "Religion and Culture",
    "Science, Technology, and Society",
    "Sociology",
    # College of Natural Resources and Environment
    "Fish and Wildlife Conservation",
    "Forest Resources and Environmental Conservation",
    "Geography",
    "Sustainable Biomaterials",
    # College of Science
    "Biological Sciences",
    "Chemistry",
    "Data Science",
    "Economics",
    "Geosciences",
    "Mathematics",
    "Neuroscience",
    "Physics",
    "Psychology",
    "Statistics",
    # Virginia-Maryland College of Veterinary Medicine
    "Veterinary Medicine",
    # Fallback
    UNKNOWN_DEPARTMENT,
]
