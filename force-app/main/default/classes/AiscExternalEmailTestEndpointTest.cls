@IsTest
private class AiscExternalEmailTestEndpointTest {
    private static void setRequest(String body) {
        RestRequest request = new RestRequest();
        request.requestUri = '/services/apexrest/aisc-external-email-test';
        request.httpMethod = 'POST';
        request.requestBody = Blob.valueOf(body);
        RestContext.request = request;
        RestContext.response = new RestResponse();
    }

    @IsTest
    static void rejects_non_allowlisted_recipient_without_sending() {
        setRequest('{"recipient":"person@example.com"}');

        Test.startTest();
        AiscExternalEmailTestEndpoint.doPost();
        Test.stopTest();

        System.assertEquals(400, RestContext.response.statusCode);
        System.assertEquals('{"outcome":"rejected"}', RestContext.response.responseBody.toString());
        System.assertEquals(0, Limits.getEmailInvocations());
    }

    @IsTest
    static void rejects_extra_payload_fields() {
        setRequest('{"recipient":"boyle@aisc.org","subject":"not allowed"}');

        AiscExternalEmailTestEndpoint.doPost();

        System.assertEquals(400, RestContext.response.statusCode);
        System.assertEquals(0, Limits.getEmailInvocations());
    }

    @IsTest
    static void sends_only_the_fixed_message_for_approved_recipient() {
        setRequest('{"recipient":"boyle@aisc.org"}');

        Test.startTest();
        AiscExternalEmailTestEndpoint.doPost();
        Test.stopTest();

        System.assertEquals(1, Limits.getEmailInvocations());
        System.assertEquals(200, RestContext.response.statusCode);
        System.assertEquals(
            '[TEST] AISC external-email proof of concept',
            AiscExternalEmailTestEndpoint.SUBJECT
        );
        System.assertEquals(
            'This is a test of the AISC external-email proof of concept. ' +
            'No action is required. Please do not reply to this email.',
            AiscExternalEmailTestEndpoint.BODY
        );
        System.assertEquals('{"outcome":"sent"}', RestContext.response.responseBody.toString());
    }
}
