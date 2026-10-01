import httpx

from app.services.clickup import clickup_error_detail_from_response, clickup_http_exception


def test_oauth_192_message():
    response = httpx.Response(
        401,
        json={"err": "Workspace not authorized", "ECODE": "OAUTH_192"},
        request=httpx.Request("GET", "https://api.clickup.com/api/v2/folder/1"),
    )
    detail = clickup_error_detail_from_response(response)
    assert detail is not None
    assert "OAUTH_192" in detail
    assert "Team ID" in detail

    exc = clickup_http_exception(httpx.HTTPStatusError("x", request=response.request, response=response))
    assert exc.status_code == 403
    assert "OAUTH_192" in str(exc.detail)
