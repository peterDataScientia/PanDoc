"""Ensure compute providers are clearly named without altering saved routes."""
from pandoc import compute_options as options


def test_compute_choices_keep_original_session_values_and_order():
    assert options.OPTIONS == (
        "Computer A · Fast",
        "Computer B · High capacity",
        "Computer C · Backup",
    )


def test_all_computers_have_descriptive_user_labels():
    assert options.display_name(options.STREAMLIT) == "This app (default)"
    assert options.display_name(options.KAGGLE) == "Kaggle worker"
    assert options.display_name(options.GITHUB_ACTIONS) == "GitHub Actions"
    assert len(set(options.LABELS.values())) == 3
    assert all("Computer" not in options.display_name(mode) for mode in options.OPTIONS)


def test_descriptions_are_honest_about_remote_requirements():
    assert "Streamlit" in options.description(options.STREAMLIT)
    assert "configured" in options.description(options.KAGGLE)
    assert "administrator" in options.description(options.GITHUB_ACTIONS)
    assert options.display_name("Future backend") == "Future backend"


def test_runtime_picker_names_all_three_computers_without_changing_routing_values():
    assert options.runtime_label(options.STREAMLIT) == "Computer A · This app"
    assert options.runtime_label(options.KAGGLE) == "Computer B · Kaggle"
    assert options.runtime_label(options.GITHUB_ACTIONS) == "Computer C · GitHub Actions"
    assert all(options.runtime_label(value).startswith(f"Computer {letter}")
               for value, letter in zip(options.OPTIONS, "ABC"))
