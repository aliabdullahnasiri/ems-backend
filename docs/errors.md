# Application errors

Application code raises `AppError` subclasses. Validation failures use
`AppValidationError` (HTTP 422), including schema construction, `model_validate`,
`model_validate_json`, `model_validate_strings`, assignment and async validation.
The shared schema `Model` converts Pydantic failures at these boundaries. FastAPI
request/path/query parsing errors are converted by the exception handler and use
the same application response contract.

Use a specific subclass when a caller needs to distinguish a failure, for example
`AmountPrecisionError`, `InvalidFileSizeError`, `OrganizationOwnershipError` or
`NullFieldError`. Messages belong in the translation catalog:

```python
class AmountPrecisionError(AppValidationError):
    type = "amount_precision_exceeded"
    message = T("validation:amount_precision")
```

`AppError` responses include `type` and translated `message`. Validation responses
also include `errors` when field details exist. Each field detail contains only
`type`, `loc` and a translated `msg`; input values and exception context are never
included. Call `error.errors()` to inspect sanitized details in services or tests.
Do not test for Pydantic's native exception type in application schema tests.

Two-factor authentication failures retain their authentication status codes through
the existing authentication error subclasses. Operational failures (uninitialized
worker/database context, unavailable challenge storage or unsupported serialization)
use distinct `AppError` subclasses rather than validation status codes.

Add English translations to `locales/en_US/LC_MESSAGES/messages.po`, register keys
in `locales/messages.pot`, then compile catalogs with `pybabel compile -d locales`.
