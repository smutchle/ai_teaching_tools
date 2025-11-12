"""Test script to verify multi-user filename handling."""
import uuid
from pathlib import Path
import tempfile

# Simulate multiple users
def simulate_user(user_id, filename):
    """Simulate a user uploading a file."""
    session_id = str(uuid.uuid4())

    # Extract original filename
    original_filename = Path(filename).stem

    # Create session-specific temp directory
    session_temp_dir = tempfile.mkdtemp(prefix=f"noteconv_{session_id[:8]}_")
    temp_dir_path = Path(session_temp_dir)

    # Generate file paths
    pdf_path = temp_dir_path / f"{original_filename}.pdf"
    qmd_path = temp_dir_path / f"{original_filename}.qmd"

    print(f"User {user_id}: {filename}")
    print(f"  Session ID: {session_id[:8]}")
    print(f"  Temp Dir: {temp_dir_path}")
    print(f"  PDF Path: {pdf_path}")
    print(f"  QMD Path: {qmd_path}")
    print(f"  Download names:")
    print(f"    - {original_filename}.qmd")
    print(f"    - {original_filename}.pdf")
    print(f"    - {original_filename}.docx")
    print(f"    - {original_filename}.tex")
    print()

    return {
        'session_id': session_id,
        'temp_dir': session_temp_dir,
        'pdf_path': str(pdf_path),
        'qmd_path': str(qmd_path),
        'original_filename': original_filename
    }

if __name__ == "__main__":
    print("=" * 70)
    print("MULTI-USER FILENAME CONFLICT TEST")
    print("=" * 70)
    print()

    # Simulate 3 users uploading different files simultaneously
    users = [
        simulate_user(1, "lecture_notes.pdf"),
        simulate_user(2, "homework_assignment.pdf"),
        simulate_user(3, "lecture_notes.pdf"),  # Same name as user 1!
    ]

    print("=" * 70)
    print("CONFLICT ANALYSIS")
    print("=" * 70)
    print()

    # Check for conflicts
    temp_dirs = [u['temp_dir'] for u in users]
    pdf_paths = [u['pdf_path'] for u in users]
    qmd_paths = [u['qmd_path'] for u in users]

    print(f"Unique temp directories: {len(set(temp_dirs))}/{len(temp_dirs)}")
    print(f"Unique PDF paths: {len(set(pdf_paths))}/{len(pdf_paths)}")
    print(f"Unique QMD paths: {len(set(qmd_paths))}/{len(qmd_paths)}")
    print()

    if len(set(temp_dirs)) == len(temp_dirs):
        print("✓ SUCCESS: All users have isolated working directories!")
    else:
        print("✗ FAIL: Directory conflicts detected!")

    if len(set(pdf_paths)) == len(pdf_paths):
        print("✓ SUCCESS: All PDF paths are unique!")
    else:
        print("✗ FAIL: PDF path conflicts detected!")

    if len(set(qmd_paths)) == len(qmd_paths):
        print("✓ SUCCESS: All QMD paths are unique!")
    else:
        print("✗ FAIL: QMD path conflicts detected!")

    print()
    print("Note: Users 1 and 3 both uploaded 'lecture_notes.pdf' but have")
    print("different session IDs, so their files are isolated in separate directories.")
