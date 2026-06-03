// Reference source for the CloudFront viewer-request Function that gates
// the entire site (and /api/*) behind HTTP basic auth.
//
// AUTHORITATIVE COPY: this logic is inlined in template.yaml under the
// BasicAuthFunction resource, where Fn::Sub replaces ${BasicAuthBase64}
// at deploy time with the parameter value. CloudFront Functions cannot
// read CloudFormation parameters at runtime, so the credential is
// compiled into the function source.
//
// This file exists for readability and diffing — if you change the logic
// here, mirror the change into template.yaml.

function handler(event) {
  var request = event.request;
  var headers = request.headers;
  var expected = '__BASIC_AUTH_B64__'; // replaced by Fn::Sub in template.yaml
  var got = headers.authorization && headers.authorization.value;
  if (!got || got !== 'Basic ' + expected) {
    return {
      statusCode: 401,
      statusDescription: 'Unauthorized',
      headers: {
        'www-authenticate': { value: 'Basic realm="jobs"' },
        'cache-control': { value: 'no-store' }
      }
    };
  }
  return request;
}
