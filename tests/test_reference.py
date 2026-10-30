from tools.generate_reference import update


def test_generated_reference_matches_schema_and_routes():
    update(check=True)
