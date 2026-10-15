"""Explicit unit adapter for pre-existing business-logic regressions.

These are NOT HTTP authorization tests. The production gateway is exercised using
real Flask clients in test_telecom_mvp. No production bypass or app flag exists.
"""
class LegacyLogicClient:
    def __init__(self,app):self.app=app
    def request(self,path,method,**kwargs):
        with self.app.test_request_context(path,method=method,**kwargs):
            try:
                value=self.app.view_functions[__import__('flask').request.url_rule.endpoint](**__import__('flask').request.view_args)
            except Exception as error:
                value=self.app.handle_user_exception(error)
            response=self.app.process_response(self.app.make_response(value))
            response.direct_passthrough=False
            response.get_data();response.close()
            return response
    def get(self,path,**kwargs):return self.request(path,'GET',**kwargs)
    def post(self,path,**kwargs):return self.request(path,'POST',**kwargs)


def legacy_logic_client(app):return LegacyLogicClient(app)
