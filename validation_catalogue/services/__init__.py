"""Service package — import pipeline symbols from the module directly to avoid heavy import cycles at package load."""

__all__ = ["run_validation_catalogue"]


def __getattr__(name: str):
    if name == "run_validation_catalogue":
        from validation_catalogue.services.pipeline import run_validation_catalogue

        return run_validation_catalogue
    raise AttributeError(name)
