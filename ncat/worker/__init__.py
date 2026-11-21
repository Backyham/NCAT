"""Internal worker entrypoints.

Workers run in the ASR virtualenv so the web/API process never imports model
runtimes. They are not user-facing CLI commands.
"""
