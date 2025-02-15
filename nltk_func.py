from nltk import tokenize, download


def nltk_proc(text_result: str):
    try:
        sentences = tokenize.sent_tokenize(text_result)
    except LookupError:
        download("punkt")
        sentences = tokenize.sent_tokenize(text_result)
    processed_text = '\n'.join(sentences)
    return processed_text
