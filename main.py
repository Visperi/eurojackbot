from eurojackpot import EuroJackpot
from typing import List, Tuple, Dict
import requests
import datetime
import os
import sys
import discord
import boto3
import json
from pathlib import Path


intents = discord.Intents.default()
intents.message_content = True

ssm = boto3.client("ssm", region_name="eu-west-1")
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    env_variables = get_env_variables()
    channel_id = env_variables["discord_channel_id"]
    group_id = env_variables["discord_group_id"]

    # Discord allows multiple channels with same name -> use ID here instead
    channel = client.get_channel(int(channel_id))
    if not channel:
        print("Invalid or unknown Discord channel ID")
        sys.exit()

    msg = generate_discord_msg(env_variables)
    msg_with_mention = f"<@&{group_id}>\n\n{msg}"

    await channel.send(msg_with_mention)
    await client.close()


def get_investment_value(parameter_store_variable_name: str) -> int:
    result = ssm.get_parameter(Name=parameter_store_variable_name)
    return int(result["Parameter"]["Value"])


def set_investment_value(value: int, parameter_store_variable_name: str) -> None:
    ssm.put_parameter(Name=parameter_store_variable_name,
                      Overwrite=True, Value=str(value))


def get_eurojackpot_next_jackpot() -> int:
    r = requests.get(
        "https://msa.veikkaus.fi/jackpot/v1/latest-jackpot-results.json").json()
    return r["draws"]["EJACKPOT"][0]["jackpots"][0]["amount"]


def get_eurojackpot_results() -> List[EuroJackpot]:
    now = datetime.datetime.now()

    week = now.isocalendar().week
    year = now.isocalendar().year

    r = requests.get(
        f"https://www.veikkaus.fi/api/draw-results/v1/games/EJACKPOT/draws/by-week/{year}-{week}").json()

    return [EuroJackpot(e) for e in r]


def fetch_winnings(
        eurojackpot: EuroJackpot,
        guesses_primary: List[str],
        guesses_secondary: List[str],
        parameter_store_variable_name: str
) -> Tuple[int, int, int, int]:
    primary_results = eurojackpot.results[0].primary
    secondary_results = eurojackpot.results[0].secondary

    primary_hits = 0
    for number in primary_results:
        if number in guesses_primary:
            primary_hits += 1

    secondary_hits = 0
    for number in secondary_results:
        if number in guesses_secondary:
            secondary_hits += 1

    total_hits = f"{primary_hits}+{secondary_hits} oikein"

    money_won = 0
    for prize_tier in eurojackpot.prize_tiers:
        if prize_tier.name == total_hits:
            money_won = prize_tier.share_amount
            break

    investment_value_old = get_investment_value(parameter_store_variable_name)
    investment_value_new = investment_value_old - 200 + money_won
    set_investment_value(investment_value_new, parameter_store_variable_name)

    return primary_hits, secondary_hits, money_won, investment_value_new


def generate_discord_msg(env_variables) -> str:
    primary_numbers = env_variables["primary_numbers"]
    secondary_numbers = env_variables["secondary_numbers"]
    parameter_store_variable_name = env_variables["parameter_store_variable_name"]

    messages = []
    eurojackpots = get_eurojackpot_results()
    if not eurojackpots:
        return "Tuloksia ei saatu Veikkaukselta :("

    for eurojackpot in eurojackpots:
        winnings = fetch_winnings(eurojackpot, primary_numbers, secondary_numbers, parameter_store_variable_name)
        primary_hits = winnings[0]
        secondary_hits = winnings[1]
        money_won = winnings[2]
        investment_value = winnings[3]

        ejackpot_week = datetime.datetime.fromtimestamp(eurojackpot.close_time/1000).isocalendar().week
        weekday = eurojackpot.brand_name.split("-")[0]

        biggest_prize_tier = eurojackpot.biggest_prize_tier

        msg = f"W{ejackpot_week}/{weekday} {primary_hits}+{secondary_hits} oikein, " \
              f"voittoa `{int(money_won) / 100:,.2f}`€, sijoituksen tuotto ||{investment_value / 100:,.2f}||€\n\n" \
              f"Isoin voitto tuloksella {biggest_prize_tier.name} `{biggest_prize_tier.share_amount / 100:,.2f}`€\n" \
              f"Seuraava päävoitto `{get_eurojackpot_next_jackpot() / 100:,.2f}`€"

        messages.append(msg)

    return "\n--\n".join(messages)


def get_env_variables() -> Dict[str, str]:
    discord_channel_id = os.environ.get("DISCORD_CHANNEL_ID")
    discord_group_id = os.environ.get("DISCORD_GROUP_ID")

    parameter_store_variable_name = os.environ.get(
        "PARAMETER_STORE_VARIABLE_NAME")

    if not discord_channel_id or not parameter_store_variable_name or not discord_group_id:
        print("Env variables missing, exiting")
        sys.exit()

    try:
        primary_numbers = os.environ.get(
            "EUROJACKPOT_PRIMARY_NUMBERS").split(",")
        secondary_numbers = os.environ.get(
            "EUROJACKPOT_SECONDARY_NUMBERS").split(",")
    except:
        print("No eurojackpot numbers, exiting")
        sys.exit()

    return {
        "discord_channel_id": discord_channel_id,
        "discord_group_id": discord_group_id,
        "parameter_store_variable_name": parameter_store_variable_name,
        "primary_numbers": primary_numbers,
        "secondary_numbers": secondary_numbers,
    }


def lambda_handler():
    discord_key = os.environ.get("DISCORD_KEY")
    if not discord_key:
        print("No discord key, exiting")
        sys.exit()

    client.run(discord_key)


if __name__ == "__main__":
    if Path("env.json").is_file():
        env_vars_tmp = json.load(open("env.json"))
        for variable_name, variable_value in env_vars_tmp.get("Variables", {}).items():
            os.environ[variable_name] = variable_value
    lambda_handler()
