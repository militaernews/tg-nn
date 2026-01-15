import json
import yaml

# Read from files
import os

# Try to read sources.json
json_data = []
if os.path.exists('sources.json'):
    with open('sources.json', 'r', encoding='utf-8') as f:
        json_content = json.load(f)
        # Handle both single object and array
        if isinstance(json_content, list):
            json_data = json_content
        else:
            json_data = [json_content]
else:
    print("Warning: sources.json not found")
    json_data = []

# Try to read sources.yaml
yaml_string = ""
if os.path.exists('sources.yaml'):
    with open('sources.yaml', 'r', encoding='utf-8') as f:
        yaml_string = f.read()
else:
    print("Warning: sources.yaml not found")
    yaml_string = ""


def escape_sql_string(value):
    """Escape single quotes for SQL"""
    if value is None:
        return "NULL"
    return f"'{str(value).replace(chr(39), chr(39) + chr(39))}'"


def generate_sources_insert(channels):
    """Generate INSERT statement for sources table"""
    values = []

    for channel in channels:
        channel_id = channel.get('channel_id')
        channel_name = escape_sql_string(channel.get('channel_name'))
        bias = escape_sql_string(channel.get('bias', ''))
        username = escape_sql_string(channel.get('username'))
        invite = escape_sql_string(channel.get('invite'))

        # bias is NOT NULL in schema, so use empty string if None
        if bias == "NULL":
            bias = "''"

        values.append(f"  ({channel_id}, {channel_name}, {bias}, {username}, {invite}, NULL)")

    sql = "INSERT INTO sources (channel_id, channel_name, bias, username, invite, avatar)\nVALUES\n"
    sql += ",\n".join(values)
    sql += "\nON CONFLICT (channel_id) DO UPDATE SET\n"
    sql += "  channel_name = EXCLUDED.channel_name,\n"
    sql += "  bias = EXCLUDED.bias,\n"
    sql += "  username = EXCLUDED.username,\n"
    sql += "  invite = EXCLUDED.invite;"

    return sql


def generate_bloats_insert(channels):
    """Generate INSERT statement for bloats table"""
    values = []

    for channel in channels:
        channel_id = channel.get('channel_id')
        bloat_patterns = channel.get('bloat', [])

        if bloat_patterns:
            for pattern in bloat_patterns:
                pattern_escaped = escape_sql_string(pattern)
                values.append(f"  ({channel_id}, {pattern_escaped})")

    if not values:
        return "-- No bloat patterns to insert"

    sql = "INSERT INTO bloats (channel_id, pattern)\nVALUES\n"
    sql += ",\n".join(values)
    sql += "\nON CONFLICT (channel_id, pattern) DO NOTHING;"

    return sql


def process_yaml_data(yaml_string):
    """Convert YAML data to list of channel dictionaries"""
    yaml_data = yaml.safe_load(yaml_string)
    channels = []

    for channel_id, data in yaml_data.items():
        channel = {
            'channel_id': channel_id,
            'channel_name': data.get('channel_name'),
            'username': data.get('username'),
            'bias': data.get('bias'),
            'invite': data.get('invite'),
            'bloat': data.get('bloat', [])
        }
        channels.append(channel)

    return channels


def process_json_data(json_obj):
    """Convert JSON object to channel dictionary"""
    return {
        'channel_id': json_obj.get('channel_id'),
        'channel_name': json_obj.get('channel_name'),
        'username': json_obj.get('username'),
        'bias': json_obj.get('bias'),
        'invite': json_obj.get('invite'),
        'bloat': []
    }


# Main execution
if __name__ == "__main__":
    # Combine all channels
    all_channels = []

    # Add JSON data
    if json_data:
        for item in json_data:
            all_channels.append(process_json_data(item))

    # Add YAML data
    if yaml_string.strip():
        all_channels.extend(process_yaml_data(yaml_string))

    # Generate SQL
    print("-- Insert statements for sources table")
    print("-- Note: avatar field is left NULL as file IDs need to be created separately\n")
    print(generate_sources_insert(all_channels))
    print("\n")
    print("-- Insert statements for bloats table (regex patterns)")
    print("-- Only for channels that have bloat patterns defined\n")
    print(generate_bloats_insert(all_channels))