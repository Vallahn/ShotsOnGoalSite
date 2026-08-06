terraform {
  required_version = ">= 1.3.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# ==============================================================================
# 1. DYNAMODB TABLES
# ==============================================================================

resource "aws_dynamodb_table" "nhl_players" {
  name         = "NHL_Players"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "player_id"

  attribute {
    name = "player_id"
    type = "N"
  }

  attribute {
    name = "team_tricode"
    type = "S"
  }

  global_secondary_index {
    name            = "team-index"
    hash_key        = "team_tricode"
    projection_type = "ALL"
  }

  tags = {
    Environment = "Dev"
    Project     = "NHL-Analytics"
  }
}

resource "aws_dynamodb_table" "nhl_games" {
  name         = "NHL_Games"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "game_id"

  attribute {
    name = "game_id"
    type = "N"
  }

  attribute {
    name = "gsi_type"
    type = "S"
  }

  attribute {
    name = "game_date"
    type = "S"
  }

  global_secondary_index {
    name            = "date-index"
    hash_key        = "gsi_type"
    range_key       = "game_date"
    projection_type = "ALL"
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Environment = "Dev"
    Project     = "NHL-Analytics"
  }
}

resource "aws_dynamodb_table" "nhl_game_rosters" {
  name         = "NHL_GameRosters"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "game_id"
  range_key    = "player_id"

  attribute {
    name = "game_id"
    type = "N"
  }

  attribute {
    name = "player_id"
    type = "N"
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Environment = "Dev"
    Project     = "NHL-Analytics"
  }
}

resource "aws_dynamodb_table" "nhl_play_by_play" {
  name         = "NHL_PlayByPlay"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "game_id"
  range_key    = "event_id"

  attribute {
    name = "game_id"
    type = "N"
  }

  attribute {
    name = "event_id"
    type = "N"
  }

  attribute {
    name = "shooter_id"
    type = "N"
  }

  attribute {
    name = "goalie_id"
    type = "N"
  }

  attribute {
    name = "team_tricode"
    type = "S"
  }

  global_secondary_index {
    name            = "shooter-index"
    hash_key        = "shooter_id"
    range_key       = "game_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "goalie-index"
    hash_key        = "goalie_id"
    range_key       = "game_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "team-index"
    hash_key        = "team_tricode"
    range_key       = "game_id"
    projection_type = "ALL"
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Environment = "Dev"
    Project     = "NHL-Analytics"
  }
}

# ==============================================================================
# 2. IAM ROLE & POLICIES FOR LAMBDA
# ==============================================================================

resource "aws_iam_role" "lambda_exec_role" {
  name = "nhl_analytics_lambda_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "dynamodb_rw_policy" {
  name        = "nhl_analytics_dynamodb_rw_policy"
  description = "Allows Lambda read/write access to NHL DynamoDB tables"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.nhl_players.arn,
          aws_dynamodb_table.nhl_play_by_play.arn,
          aws_dynamodb_table.nhl_games.arn,
          aws_dynamodb_table.nhl_game_rosters.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_dynamodb" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = aws_iam_policy.dynamodb_rw_policy.arn
}

# ==============================================================================
# 2b. IAM ROLE & POLICY FOR THE WEBSITE'S QUERY API LAMBDA (READ-ONLY)
# ==============================================================================

resource "aws_iam_role" "query_api_exec_role" {
  name = "nhl_analytics_query_api_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "query_api_logs" {
  role       = aws_iam_role.query_api_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "dynamodb_ro_policy" {
  name        = "nhl_analytics_dynamodb_ro_policy"
  description = "Read-only access (including GSIs) for the website query API"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.nhl_players.arn,
          "${aws_dynamodb_table.nhl_players.arn}/index/*",
          aws_dynamodb_table.nhl_play_by_play.arn,
          "${aws_dynamodb_table.nhl_play_by_play.arn}/index/*",
          aws_dynamodb_table.nhl_games.arn,
          "${aws_dynamodb_table.nhl_games.arn}/index/*",
          aws_dynamodb_table.nhl_game_rosters.arn
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = [aws_dynamodb_table.nhl_players.arn]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "query_api_dynamodb" {
  role       = aws_iam_role.query_api_exec_role.name
  policy_arn = aws_iam_policy.dynamodb_ro_policy.arn
}

# ==============================================================================
# 2c. IAM ROLE & POLICY FOR THE DAILY SCHEDULE-TOGGLE LAMBDA
# ==============================================================================

resource "aws_iam_role" "schedule_toggle_exec_role" {
  name = "nhl_analytics_schedule_toggle_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "schedule_toggle_logs" {
  role       = aws_iam_role.schedule_toggle_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "eventbridge_toggle_policy" {
  name        = "nhl_analytics_eventbridge_toggle_policy"
  description = "Allows enabling/disabling only the pbp_ingest_schedule rule -- nothing else"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["events:EnableRule", "events:DisableRule", "events:DescribeRule"]
        Resource = [aws_cloudwatch_event_rule.pbp_ingest_schedule.arn]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "schedule_toggle_events" {
  role       = aws_iam_role.schedule_toggle_exec_role.name
  policy_arn = aws_iam_policy.eventbridge_toggle_policy.arn
}

# ==============================================================================
# 3. BUILD DEPENDENCIES & PACKAGE LAMBDAS
# ==============================================================================

resource "null_resource" "install_dependencies" {
  triggers = {
    always_run = "${timestamp()}"
  }

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-Command"]
    command     = "If (!(Test-Path -Path './build')) { New-Item -ItemType Directory -Path './build' }; pip install requests -t ./build"
  }
}

resource "null_resource" "copy_sources" {
  depends_on = [null_resource.install_dependencies]

  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-Command"]
    command     = "Copy-Item -Path './src/*.py' -Destination './build/' -Force"
  }
}

data "archive_file" "lambda_package" {
  depends_on  = [null_resource.copy_sources]
  type        = "zip"
  source_dir  = "${path.module}/build"
  output_path = "${path.module}/dist/nhl_analytics_package.zip"
}

# ==============================================================================
# 4. LAMBDA FUNCTIONS
# ==============================================================================

resource "aws_lambda_function" "roster_sync" {
  filename         = data.archive_file.lambda_package.output_path
  function_name    = "nhl_roster_sync"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "lambda_roster_sync.lambda_handler"
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  tags = {
    Project = "NHL-Analytics"
  }
}

resource "aws_lambda_function" "pbp_ingest" {
  filename         = data.archive_file.lambda_package.output_path
  function_name    = "nhl_pbp_ingest"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "lambda_pbp_ingest.lambda_handler"
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  tags = {
    Project = "NHL-Analytics"
  }
}

resource "aws_lambda_function" "query_api" {
  filename         = data.archive_file.lambda_package.output_path
  function_name    = "nhl_query_api"
  role             = aws_iam_role.query_api_exec_role.arn
  handler          = "lambda_query_api.lambda_handler"
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 25
  memory_size      = 256

  environment {
    variables = {
      PBP_TABLE_NAME          = aws_dynamodb_table.nhl_play_by_play.name
      PLAYERS_TABLE_NAME      = aws_dynamodb_table.nhl_players.name
      GAMES_TABLE_NAME        = aws_dynamodb_table.nhl_games.name
      GAME_ROSTERS_TABLE_NAME = aws_dynamodb_table.nhl_game_rosters.name
      CORS_ALLOW_ORIGIN       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
    }
  }

  tags = {
    Project = "NHL-Analytics"
  }
}

resource "aws_lambda_function" "schedule_toggle" {
  filename         = data.archive_file.lambda_package.output_path
  function_name    = "nhl_schedule_toggle"
  role             = aws_iam_role.schedule_toggle_exec_role.arn
  handler          = "lambda_schedule_toggle.lambda_handler"
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 15
  memory_size      = 128

  tags = {
    Project = "NHL-Analytics"
  }
}

# ==============================================================================
# 5. CLOUDWATCH LOG GROUPS & IMPORT
# ==============================================================================

resource "aws_cloudwatch_log_group" "roster_sync_logs" {
  name              = "/aws/lambda/nhl_roster_sync"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "pbp_ingest_logs" {
  name              = "/aws/lambda/nhl_pbp_ingest"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "query_api_logs" {
  name              = "/aws/lambda/nhl_query_api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "schedule_toggle_logs" {
  name              = "/aws/lambda/nhl_schedule_toggle"
  retention_in_days = 7
}

# ==============================================================================
# 6. EVENTBRIDGE SCHEDULED TRIGGERS
# ==============================================================================

resource "aws_cloudwatch_event_rule" "weekly_roster_schedule" {
  name                = "nhl_weekly_roster_sync_schedule"
  description         = "Triggers Roster Delta Sync every Sunday at 00:00 UTC"
  schedule_expression = "cron(0 0 ? * SUN *)"
}

resource "aws_cloudwatch_event_target" "roster_sync_target" {
  rule      = aws_cloudwatch_event_rule.weekly_roster_schedule.name
  target_id = "TriggerRosterSyncLambda"
  arn       = aws_lambda_function.roster_sync.arn
}

resource "aws_lambda_permission" "allow_eventbridge_roster" {
  statement_id  = "AllowExecutionFromEventBridgeWeekly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.roster_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_roster_schedule.arn
}

resource "aws_cloudwatch_event_rule" "pbp_ingest_schedule" {
  name                = "nhl_pbp_ingest_schedule"
  description         = "Triggers PBP Ingestion every 1 min during evening game windows"
  schedule_expression = "cron(* 18-06 ? * * *)"

  lifecycle {
    ignore_changes = [is_enabled]
  }
}

resource "aws_cloudwatch_event_target" "pbp_ingest_target" {
  rule      = aws_cloudwatch_event_rule.pbp_ingest_schedule.name
  target_id = "TriggerPBPIngestLambda"
  arn       = aws_lambda_function.pbp_ingest.arn
}

resource "aws_lambda_permission" "allow_eventbridge_pbp" {
  statement_id  = "AllowExecutionFromEventBridgePBP"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pbp_ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pbp_ingest_schedule.arn
}

resource "aws_cloudwatch_event_rule" "daily_schedule_check" {
  name                = "nhl_daily_schedule_check"
  description         = "Once daily: enables/disables pbp_ingest_schedule based on whether any games are on today's slate"
  schedule_expression = "cron(0 12 * * ? *)" # 12:00 UTC, well before the 18:00 UTC ingest window starts
}

resource "aws_cloudwatch_event_target" "schedule_toggle_target" {
  rule      = aws_cloudwatch_event_rule.daily_schedule_check.name
  target_id = "TriggerScheduleToggleLambda"
  arn       = aws_lambda_function.schedule_toggle.arn
}

resource "aws_lambda_permission" "allow_eventbridge_schedule_toggle" {
  statement_id  = "AllowExecutionFromEventBridgeDailyCheck"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.schedule_toggle.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_schedule_check.arn
}

# ==============================================================================
# 7. HTTP API (API GATEWAY) FOR THE WEBSITE'S QUERY LAMBDA
# ==============================================================================

resource "aws_apigatewayv2_api" "shotmap_api" {
  name          = "nhl-shotmap-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.frontend.domain_name}"]
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["Content-Type"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_stage" "shotmap_api_stage" {
  api_id      = aws_apigatewayv2_api.shotmap_api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_apigatewayv2_integration" "query_api_integration" {
  api_id                 = aws_apigatewayv2_api.shotmap_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query_api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "games_route" {
  api_id    = aws_apigatewayv2_api.shotmap_api.id
  route_key = "GET /games"
  target    = "integrations/${aws_apigatewayv2_integration.query_api_integration.id}"
}

resource "aws_apigatewayv2_route" "teams_route" {
  api_id    = aws_apigatewayv2_api.shotmap_api.id
  route_key = "GET /teams"
  target    = "integrations/${aws_apigatewayv2_integration.query_api_integration.id}"
}

resource "aws_apigatewayv2_route" "players_route" {
  api_id    = aws_apigatewayv2_api.shotmap_api.id
  route_key = "GET /players"
  target    = "integrations/${aws_apigatewayv2_integration.query_api_integration.id}"
}

resource "aws_apigatewayv2_route" "game_players_route" {
  api_id    = aws_apigatewayv2_api.shotmap_api.id
  route_key = "GET /game_players"
  target    = "integrations/${aws_apigatewayv2_integration.query_api_integration.id}"
}

resource "aws_apigatewayv2_route" "shots_route" {
  api_id    = aws_apigatewayv2_api.shotmap_api.id
  route_key = "GET /shots"
  target    = "integrations/${aws_apigatewayv2_integration.query_api_integration.id}"
}

resource "aws_lambda_permission" "allow_apigw_query_api" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.shotmap_api.execution_arn}/*/*"
}

# ==============================================================================
# 8. STATIC FRONTEND (S3 + CLOUDFRONT)
# ==============================================================================

resource "random_id" "frontend_bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "frontend" {
  bucket = "nhl-shotmap-frontend-${random_id.frontend_bucket_suffix.hex}"

  tags = {
    Project = "NHL-Analytics"
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "nhl-shotmap-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "nhl-shotmap-s3-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "nhl-shotmap-s3-origin"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Project = "NHL-Analytics"
  }
}

data "aws_iam_policy_document" "frontend_bucket_policy" {
  statement {
    sid       = "AllowCloudFrontServicePrincipal"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket_policy.json
}

# ==============================================================================
# 9. OUTPUTS
# ==============================================================================

output "players_table_name" {
  value = aws_dynamodb_table.nhl_players.name
}

output "pbp_table_name" {
  value = aws_dynamodb_table.nhl_play_by_play.name
}

output "games_table_name" {
  value = aws_dynamodb_table.nhl_games.name
}

output "api_endpoint" {
  description = "Base URL for the query API. Put this in the frontend's VITE_API_BASE_URL."
  value       = aws_apigatewayv2_api.shotmap_api.api_endpoint
}

output "frontend_bucket_name" {
  description = "Upload the built React app (npm run build -> dist/) to this bucket."
  value       = aws_s3_bucket.frontend.bucket
}

output "cloudfront_domain_name" {
  description = "The public URL of the shot map website."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}