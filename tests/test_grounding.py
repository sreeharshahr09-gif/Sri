from analyst_agent.grounding import check_grounding


def test_rounded_values_are_traced():
    report = check_grounding("Mean revenue is 1,234.57 and the median 980.", ["mean 1234.5678\nmedian 980.0"])
    assert report.checked == ["1,234.57", "980"] and report.fully_traced


def test_percentages_match_fractions_or_percent_values():
    evidence = ["share 0.4532", "growth_pct 12.0"]
    assert check_grounding("West has 45.3% of sales and grew 12%.", evidence).fully_traced


def test_scaled_suffixes():
    assert check_grounding("Revenue reached $1.2M.", ["1234567.0"]).fully_traced
    assert not check_grounding("Revenue reached $1.5M.", ["1234567.0"]).fully_traced


def test_untraced_numbers_reported():
    report = check_grounding("The correlation is 0.82.", ["r = 0.65"])
    assert report.untraced == ["0.82"]


def test_ignored_tokens():
    answer = "1. In 2023 there were 3 regions, p < 0.05, see `df.head(10)`. Question asked about 50 rows."
    report = check_grounding(answer, [""], question="Show me 50 rows")
    assert report.checked == []


def test_negative_and_scientific_numbers():
    assert check_grounding("The slope is -2.35 (p = 1.2e-05).", ["coef -2.3456 pvalue 1.1984e-05"]).fully_traced
