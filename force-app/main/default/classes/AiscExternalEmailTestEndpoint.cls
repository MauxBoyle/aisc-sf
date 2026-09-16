/**
 * A deliberately narrow REST endpoint for the external-email proof of concept.
 * It owns both the allowlist and message text; API clients provide only recipient.
 */
@RestResource(urlMapping='/aisc-external-email-test')
global with sharing class AiscExternalEmailTestEndpoint {
    @TestVisible private static final Set<String> APPROVED_RECIPIENTS = new Set<String>{
        'boyle@aisc.org',
        'mauxboyle@gmail.com',
        'maureen7780@yahoo.com'
    };
    @TestVisible private static final String SUBJECT =
        '[TEST] AISC external-email proof of concept';
    @TestVisible private static final String BODY =
        'This is a test of the AISC external-email proof of concept. ' +
        'No action is required. Please do not reply to this email.';

    global static void doPost() {
        RestResponse response = RestContext.response;
        String recipient = recipientFromRequest(RestContext.request);
        if (recipient == null || !APPROVED_RECIPIENTS.contains(recipient)) {
            respond(response, 400, 'rejected');
            return;
        }

        try {
            Messaging.SingleEmailMessage message = new Messaging.SingleEmailMessage();
            message.setToAddresses(new String[]{recipient});
            message.setSubject(SUBJECT);
            message.setPlainTextBody(BODY);
            Messaging.SendEmailResult[] results = Messaging.sendEmail(
                new Messaging.SingleEmailMessage[]{message}
            );
            if (results.size() == 1 && results[0].isSuccess()) {
                respond(response, 200, 'sent');
            } else {
                respond(response, 502, 'failed');
            }
        } catch (Exception error) {
            // Do not expose provider details in this intentionally safe endpoint.
            respond(response, 500, 'failed');
        }
    }

    @TestVisible
    private static String recipientFromRequest(RestRequest request) {
        if (request == null || request.requestBody == null) {
            return null;
        }
        try {
            Object decoded = JSON.deserializeUntyped(request.requestBody.toString());
            if (!(decoded instanceof Map<String, Object>)) {
                return null;
            }
            Map<String, Object> payload = (Map<String, Object>)decoded;
            if (payload.size() != 1 || !payload.containsKey('recipient')) {
                return null;
            }
            Object value = payload.get('recipient');
            return value instanceof String ? (String)value : null;
        } catch (Exception error) {
            return null;
        }
    }

    private static void respond(RestResponse response, Integer statusCode, String outcome) {
        response.statusCode = statusCode;
        response.addHeader('Content-Type', 'application/json');
        response.responseBody = Blob.valueOf('{"outcome":"' + outcome + '"}');
    }
}
