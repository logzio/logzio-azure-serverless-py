import logging
import azure.functions as func

app = func.FunctionApp()

@app.function_name(name="EventHubTrigger1")
@app.event_hub_message_trigger(arg_name="myhub", 
                               event_hub_name="logzio-evnthub-serverless-test",
                               connection="Endpoint=sb://logzio-namespace-serverless-test.servicebus.windows.net/;SharedAccessKeyName=severless-test;SharedAccessKey=8R/I1L1KMU5bF5lGDnK9aEK8Q8mifC75f+AEhI6jUdE=;EntityPath=logzio-evnthub-serverless-test") 
def test_function(myhub: func.EventHubEvent):
    logging.info('Python EventHub trigger processed an event: %s',
                myhub.get_body().decode('utf-8'))