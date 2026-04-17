from app.services.assignment_matcher import AssignmentMatcherService

service = AssignmentMatcherService()
results = service.match_all()

matched_exact = sum(1 for r in results if r.match_status == "matched_exact_position_code")
matched_refined = sum(1 for r in results if r.match_status == "matched_refined")
ambiguous = sum(1 for r in results if r.match_status == "ambiguous_multiple_candidates")
no_match = sum(1 for r in results if r.match_status == "no_match")

print(f"Asignaciones procesadas: {len(results)}")
print(f"Matched exactos: {matched_exact}")
print(f"Matched refinados: {matched_refined}")
print(f"Ambiguos: {ambiguous}")
print(f"Sin match: {no_match}")