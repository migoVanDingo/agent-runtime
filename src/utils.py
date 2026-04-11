import ulid


def resource_prefix(resource: str):
    resource_list = {"message": "MSG", "session": "SES"}
    return resource_list[resource]


def generate_id(resource: str):
    prefix = resource_prefix(resource)
    return prefix + str(ulid.new())
