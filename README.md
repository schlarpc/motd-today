# motd.today

This is a quick port of the existing https://motd.today site to run with AWS serverless tech.
It's not polished or well packaged, but it (mostly) works. 

Rough deployment outline (no script yet):
```
DEPLOYMENT_BUCKET=...
git clone https://github.com/schlarpc/overengineered-cloudfront-s3-static-website
cd overengineered-cloudfront-s3-static-website
python3 -m overengineered_cloudfront_s3_static_website \
    > ..\website.json
cd -
python3 -m app.template website.json > motd-today.json
python3 -m awscli --region us-east-1 cloudformation package \
    --template-file motd-today.json --s3-bucket ... --use-json \
    > deployment.json
python3 -m awscli --region us-east-1 cloudformation deploy \
    --template-file deployment.json --stack-name motd-today --capabilities CAPABILITY_IAM \
    --parameter-overrides SmiteDeveloperId=... SmiteAuthKey=... \
    TwitterConsumerKey=... TwitterConsumerSecret=... TwitterAccessKey=... TwitterAccessSecret=...
python3 -m awscli s3 sync html/ s3://...

```

