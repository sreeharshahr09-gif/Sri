from analyst_agent.parsing import parse_reply, strip_thinking


def test_fenced_python_block_is_code():
    reply = parse_reply("Let me check.\n```python\nprint(df.shape)\n```")
    assert reply.code == "print(df.shape)"
    assert reply.thought == "Let me check."
    assert reply.final_answer is None


def test_multiple_blocks_are_joined_in_order():
    reply = parse_reply("```python\na = 1\n```\nthen\n```py\nprint(a)\n```")
    assert reply.code == "a = 1\n\nprint(a)"


def test_unlabelled_fence_only_used_when_valid_python():
    assert parse_reply("```\nx = df['a'].sum()\n```").code == "x = df['a'].sum()"
    reply = parse_reply("Here is a table:\n```\n| a | b |\n|---|---|\n```\nFinal Answer: done")
    assert reply.code is None
    assert reply.final_answer == "done"


def test_non_python_language_blocks_ignored():
    reply = parse_reply("```json\n{\"a\": 1}\n```\nFinal Answer: 42 rows")
    assert reply.code is None
    assert reply.final_answer == "42 rows"


def test_final_answer_variants():
    for text in ("Final Answer: 5", "**Final Answer:** 5", "## Final Answer\n5", "final answer: 5"):
        assert parse_reply(text).final_answer == "5", text


def test_final_answer_requires_marker_word_boundary():
    reply = parse_reply("Final answers are hard.\nThe mean is 3.")
    assert reply.final_answer == "Final answers are hard.\nThe mean is 3."


def test_code_wins_over_premature_final_answer():
    reply = parse_reply("```python\nprint(1)\n```\nFinal Answer: the result is 1")
    assert reply.code == "print(1)"
    assert reply.final_answer is None


def test_code_inside_final_answer_is_not_executed():
    reply = parse_reply("Final Answer: use this:\n```python\ndf.mean()\n```")
    assert reply.code is None
    assert "df.mean()" in reply.final_answer


def test_unclosed_fence_flagged_as_truncated():
    reply = parse_reply("Checking.\n```python\nfor x in range(10):\n    print(")
    assert reply.truncated_code
    assert reply.code is None


def test_thinking_blocks_removed():
    assert strip_thinking("<think>hmm ```python\nbad()\n```</think>Answer") == "Answer"
    reply = parse_reply("<think>plan</think>\n```python\nprint(2)\n```")
    assert reply.code == "print(2)"


def test_console_prompts_stripped():
    assert parse_reply("```python\n>>> x = 1\n>>> print(x)\n```").code == "x = 1\nprint(x)"


def test_legacy_code_marker():
    reply = parse_reply("- Answer: a chart\n- Code: import plotly.express as px\nfig = px.bar(df, x='a', y='b')")
    assert reply.code == "import plotly.express as px\nfig = px.bar(df, x='a', y='b')"


def test_plain_prose_is_final_answer():
    assert parse_reply("Hello! Upload data and ask me.").final_answer == "Hello! Upload data and ask me."
