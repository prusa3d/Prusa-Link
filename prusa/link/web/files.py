"""Check and modify an input dictionary using recursion"""

from datetime import datetime

sd_card_name = "SD Card"
sd_card_source = sd_card_name
prusa_link_name = "Prusa Link gcodes"
prusa_link_source = "local"

# TODO - get values from SDK
allowed_extensions = (".gcode", ".gco")


def iterate_node(dict_input):
    """Iterate through dictionary and check / modify it"""
    # Rename keys, according OctoPrint standard
    if "m_time" in dict_input:
        dict_input["date"] = dict_input.pop("m_time")

    # New value, according OctoPrint standard
    if "typePath" not in dict_input:
        dict_input["typePath"] = []

    # New value, according OctoPrint standard
    if "origin" not in dict_input:
        dict_input["origin"] = None

    # Rename keys, add values, according OctoPrint standard
    for key, value in dict_input.items():
        if key == "type":
            if dict_input["type"] == "DIR":
                dict_input["type"] = "FOLDER"
                dict_input["typePath"] = ["folder"]

            elif dict_input["type"] == "FILE" and dict_input["name"].endswith(allowed_extensions):
                dict_input["type"] = "machinecode"
                dict_input["typePath"] = ["machinecode", "gcode"]

        if key == "date":
            if type(dict_input[key]) is tuple or type(dict_input[key]) is list:
                dict_input[key] = int(datetime.timestamp(datetime(*dict_input[key])))

        if key == "children":
            for i in dict_input[key]:
                iterate_node(i)

        # Change origin name according parent
        if key == "origin":
            if dict_input["name"] == sd_card_source:
                dict_input["origin"] = sd_card_source

            elif dict_input["name"] == prusa_link_name:
                dict_input["origin"] = prusa_link_source

            if dict_input["type"] == "FOLDER":
                change_origin(dict_input, sd_card_source)
                change_origin(dict_input, prusa_link_source)


def change_origin(input_dictionary, parent_origin):
    """Change the children origin name, according parent"""
    if input_dictionary["origin"] == parent_origin:
        for i in input_dictionary["children"]:
            for k, v in i.items():
                i["origin"] = parent_origin
                if k == "children":
                    for j in input_dictionary[k]:
                        iterate_node(j)


def files_to_api(input_data):
    """Iterate through n-dictionaries with n-lists and return modified data"""
    iterate_node(input_data)
    return input_data
