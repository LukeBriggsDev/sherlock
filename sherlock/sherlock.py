#! /usr/bin/env python3

"""
Sherlock: Find Usernames Across Social Networks Module

This module contains the main logic to search for usernames at social
networks.
"""

import csv
import json
import os
import platform
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from time import monotonic

import requests
from colorama import Fore, Style, init

from requests_futures.sessions import FuturesSession
from torrequest import TorRequest
from result import QueryStatus
from result import QueryResult
from sites  import SitesInformation

module_name = "Sherlock: Find Usernames Across Social Networks"
__version__ = "0.10.5"



class SherlockFuturesSession(FuturesSession):
    def request(self, method, url, hooks={}, *args, **kwargs):
        """Request URL.

        This extends the FuturesSession request method to calculate a response
        time metric to each request.

        It is taken (almost) directly from the following StackOverflow answer:
        https://github.com/ross/requests-futures#working-in-the-background

        Keyword Arguments:
        self                   -- This object.
        method                 -- String containing method desired for request.
        url                    -- String containing URL for request.
        hooks                  -- Dictionary containing hooks to execute after
                                  request finishes.
        args                   -- Arguments.
        kwargs                 -- Keyword arguments.

        Return Value:
        Request object.
        """
        #Record the start time for the request.
        start = monotonic()

        def response_time(resp, *args, **kwargs):
            """Response Time Hook.

            Keyword Arguments:
            resp                   -- Response object.
            args                   -- Arguments.
            kwargs                 -- Keyword arguments.

            Return Value:
            N/A
            """
            resp.elapsed = monotonic() - start

            return

        #Install hook to execute when response completes.
        #Make sure that the time measurement hook is first, so we will not
        #track any later hook's execution time.
        try:
            if isinstance(hooks['response'], list):
                hooks['response'].insert(0, response_time)
            elif isinstance(hooks['response'], tuple):
                #Convert tuple to list and insert time measurement hook first.
                hooks['response'] = list(hooks['response'])
                hooks['response'].insert(0, response_time)
            else:
                #Must have previously contained a single hook function,
                #so convert to list.
                hooks['response'] = [response_time, hooks['response']]
        except KeyError:
            #No response hook was already defined, so install it ourselves.
            hooks['response'] = [response_time]

        return super(SherlockFuturesSession, self).request(method,
                                                           url,
                                                           hooks=hooks,
                                                           *args, **kwargs)


def print_info(title, info, color=True):
    if color:
        print(Style.BRIGHT + Fore.GREEN + "[" +
            Fore.YELLOW + "*" +
            Fore.GREEN + f"] {title}" +
            Fore.WHITE + f" {info}" +
            Fore.GREEN + " on:")
    else:
        print(f"[*] {title} {info} on:")

def print_error(social_network, err, errstr, var, verbose=False, color=True):
    if color:
        print(Style.BRIGHT + Fore.WHITE + "[" +
            Fore.RED + "-" +
            Fore.WHITE + "]" +
            Fore.GREEN + f" {social_network}:" +
            Fore.RED + f" {errstr}" +
            Fore.YELLOW + f" {err if verbose else var}")
    else:
        print(f"[-] {social_network}: {errstr} {err if verbose else var}")


def format_response_time(response_time, verbose):
    return f" [{round(response_time * 1000)} ms]" if verbose else ""


def print_found(social_network, url, response_time, verbose=False, color=True):
    if color:
        print((Style.BRIGHT + Fore.WHITE + "[" +
            Fore.GREEN + "+" +
            Fore.WHITE + "]" +
            format_response_time(response_time, verbose) +
            Fore.GREEN + f" {social_network}:"), url)
    else:
        print(f"[+]{format_response_time(response_time, verbose)} {social_network}: {url}")

def print_not_found(social_network, response_time, verbose=False, color=True):
    if color:
        print((Style.BRIGHT + Fore.WHITE + "[" +
            Fore.RED + "-" +
            Fore.WHITE + "]" +
            format_response_time(response_time, verbose) +
            Fore.GREEN + f" {social_network}:" +
            Fore.YELLOW + " Not Found!"))
    else:
        print(f"[-]{format_response_time(response_time, verbose)} {social_network}: Not Found!")

def print_invalid(social_network, msg, color=True):
    """Print invalid search result."""
    if color:
        print((Style.BRIGHT + Fore.WHITE + "[" +
            Fore.RED + "-" +
            Fore.WHITE + "]" +
            Fore.GREEN + f" {social_network}:" +
            Fore.YELLOW + f" {msg}"))
    else:
        print(f"[-] {social_network} {msg}")


def get_response(request_future, error_type, social_network, verbose=False, color=True):

    #Default for Response object if some failure occurs.
    response = None

    error_context = "General Unknown Error"
    expection_text = None
    try:
        response = request_future.result()
        if response.status_code:
            #status code exists in response object
            error_context = None
    except requests.exceptions.HTTPError as errh:
        error_context = "HTTP Error"
        expection_text = str(errh)
    except requests.exceptions.ProxyError as errp:
        error_context = "Proxy Error"
        expection_text = str(errp)
    except requests.exceptions.ConnectionError as errc:
        error_context = "Error Connecting"
        expection_text = str(errc)
    except requests.exceptions.Timeout as errt:
        error_context = "Timeout Error"
        expection_text = str(errt)
    except requests.exceptions.RequestException as err:
        error_context = "Unknown Error"
        expection_text = str(err)

    return response, error_context, expection_text


def sherlock(username, site_data, verbose=False, tor=False, unique_tor=False,
             proxy=None, print_found_only=False, timeout=None, color=True,
             print_output=True):
    """Run Sherlock Analysis.

    Checks for existence of username on various social media sites.

    Keyword Arguments:
    username               -- String indicating username that report
                              should be created against.
    site_data              -- Dictionary containing all of the site data.
    verbose                -- Boolean indicating whether to give verbose output.
    tor                    -- Boolean indicating whether to use a tor circuit for the requests.
    unique_tor             -- Boolean indicating whether to use a new tor circuit for each request.
    proxy                  -- String indicating the proxy URL
    print_found_only       -- Boolean indicating whether to only print found sites.
    timeout                -- Time in seconds to wait before timing out request.
                              Default is no timeout.
    color                  -- Boolean indicating whether to color terminal output
    print_output           -- Boolean indicating whether the output should be
                              printed.  Default is True.

    Return Value:
    Dictionary containing results from report. Key of dictionary is the name
    of the social network site, and the value is another dictionary with
    the following keys:
        url_main:      URL of main site.
        url_user:      URL of user on site (if account exists).
        status:        QueryResult() object indicating results of test for
                       account existence.
        http_status:   HTTP status code of query which checked for existence on
                       site.
        response_text: Text that came back from request.  May be None if
                       there was an HTTP error when checking for existence.
    """
    if print_output == True:
        print_info("Checking username", username, color)

    # Create session based on request methodology
    if tor or unique_tor:
        #Requests using Tor obfuscation
        underlying_request = TorRequest()
        underlying_session = underlying_request.session
    else:
        #Normal requests
        underlying_session = requests.session()
        underlying_request = requests.Request()

    #Limit number of workers to 20.
    #This is probably vastly overkill.
    if len(site_data) >= 20:
        max_workers=20
    else:
        max_workers=len(site_data)

    #Create multi-threaded session for all requests.
    session = SherlockFuturesSession(max_workers=max_workers,
                                     session=underlying_session)


    # Results from analysis of all sites
    results_total = {}

    # First create futures for all requests. This allows for the requests to run in parallel
    for social_network, net_info in site_data.items():

        # Results from analysis of this specific site
        results_site = {}

        # Record URL of main site
        results_site['url_main'] = net_info.get("urlMain")

        # A user agent is needed because some sites don't return the correct
        # information since they think that we are bots (Which we actually are...)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.12; rv:55.0) Gecko/20100101 Firefox/55.0',
        }

        if "headers" in net_info:
            # Override/append any extra headers required by a given site.
            headers.update(net_info["headers"])

        # Don't make request if username is invalid for the site
        regex_check = net_info.get("regexCheck")
        if regex_check and re.search(regex_check, username) is None:
            # No need to do the check at the site: this user name is not allowed.
            if (print_output == True) and not print_found_only:
                print_invalid(social_network, "Illegal Username Format For This Site!", color)

            results_site['status'] = QueryResult(QueryStatus.ILLEGAL)
            results_site["url_user"] = ""
            results_site['http_status'] = ""
            results_site['response_text'] = ""
            results_site['response_time_s'] = ""
        else:
            # URL of user on site (if it exists)
            url = net_info["url"].format(username)
            results_site["url_user"] = url
            url_probe = net_info.get("urlProbe")
            if url_probe is None:
                # Probe URL is normal one seen by people out on the web.
                url_probe = url
            else:
                # There is a special URL for probing existence separate
                # from where the user profile normally can be found.
                url_probe = url_probe.format(username)

            #If only the status_code is needed don't download the body
            if net_info["errorType"] == 'status_code':
                request_method = session.head
            else:
                request_method = session.get

            if net_info["errorType"] == "response_url":
                # Site forwards request to a different URL if username not
                # found.  Disallow the redirect so we can capture the
                # http status from the original URL request.
                allow_redirects = False
            else:
                # Allow whatever redirect that the site wants to do.
                # The final result of the request will be what is available.
                allow_redirects = True

            # This future starts running the request in a new thread, doesn't block the main thread
            if proxy != None:
                proxies = {"http": proxy, "https": proxy}
                future = request_method(url=url_probe, headers=headers,
                                        proxies=proxies,
                                        allow_redirects=allow_redirects,
                                        timeout=timeout
                                        )
            else:
                future = request_method(url=url_probe, headers=headers,
                                        allow_redirects=allow_redirects,
                                        timeout=timeout
                                        )

            # Store future in data for access later
            net_info["request_future"] = future

            # Reset identify for tor (if needed)
            if unique_tor:
                underlying_request.reset_identity()

        # Add this site's results into final dictionary with all of the other results.
        results_total[social_network] = results_site

    # Open the file containing account links
    # Core logic: If tor requests, make them here. If multi-threaded requests, wait for responses
    for social_network, net_info in site_data.items():

        # Retrieve results again
        results_site = results_total.get(social_network)

        # Retrieve other site information again
        url = results_site.get("url_user")
        status = results_site.get("status")
        if status is not None:
            # We have already determined the user doesn't exist here
            continue

        # Get the expected error type
        error_type = net_info["errorType"]

        # Retrieve future and ensure it has finished
        future = net_info["request_future"]
        r, error_text, expection_text = get_response(request_future=future,
                                                     error_type=error_type,
                                                     social_network=social_network,
                                                     verbose=verbose,
                                                     color=color)

        #Get response time for response of our request.
        try:
            response_time = r.elapsed
        except AttributeError:
            response_time = None

        # Attempt to get request information
        try:
            http_status = r.status_code
        except:
            http_status = "?"
        try:
            response_text = r.text.encode(r.encoding)
        except:
            response_text = ""

        if error_text is not None:
            result = QueryResult(QueryStatus.UNKNOWN, error_text)
        elif error_type == "message":
            error = net_info.get("errorMsg")
            # Checks if the error message is in the HTML
            if not error in r.text:
                result = QueryResult(QueryStatus.CLAIMED)
            else:
                result = QueryResult(QueryStatus.AVAILABLE)
        elif error_type == "status_code":
            # Checks if the status code of the response is 2XX
            if not r.status_code >= 300 or r.status_code < 200:
                result = QueryResult(QueryStatus.CLAIMED)
            else:
                result = QueryResult(QueryStatus.AVAILABLE)
        elif error_type == "response_url":
            # For this detection method, we have turned off the redirect.
            # So, there is no need to check the response URL: it will always
            # match the request.  Instead, we will ensure that the response
            # code indicates that the request was successful (i.e. no 404, or
            # forward to some odd redirect).
            if 200 <= r.status_code < 300:
                result = QueryResult(QueryStatus.CLAIMED)
            else:
                result = QueryResult(QueryStatus.AVAILABLE)
        else:
            #It should be impossible to ever get here...
            raise ValueError(f"Unknown Error Type '{error_type}' for "
                             f"site '{social_network}'")


        if print_output == True:
            #Output to the terminal is desired.
            if result.status == QueryStatus.CLAIMED:
                print_found(social_network, url, response_time, verbose, color)
            elif result.status == QueryStatus.AVAILABLE:
                if not print_found_only:
                    print_not_found(social_network, response_time, verbose, color)
            elif result.status == QueryStatus.UNKNOWN:
                print_error(social_network, expection_text, error_text, "", verbose, color)
            elif result.status == QueryStatus.ILLEGAL:
                if not print_found_only:
                    print_invalid(social_network, "Illegal Username Format For This Site!", color)
            else:
                #It should be impossible to ever get here...
                raise ValueError(f"Unknown Query Status '{str(result.status)}' for "
                                 f"site '{social_network}'")


        # Save status of request
        results_site['status'] = result

        # Save results from request
        results_site['http_status'] = http_status
        results_site['response_text'] = response_text
        results_site['response_time_s'] = response_time

        # Add this site's results into final dictionary with all of the other results.
        results_total[social_network] = results_site
    return results_total


def timeout_check(value):
    """Check Timeout Argument.

    Checks timeout for validity.

    Keyword Arguments:
    value                  -- Time in seconds to wait before timing out request.

    Return Value:
    Floating point number representing the time (in seconds) that should be
    used for the timeout.

    NOTE:  Will raise an exception if the timeout in invalid.
    """
    from argparse import ArgumentTypeError

    try:
        timeout = float(value)
    except:
        raise ArgumentTypeError(f"Timeout '{value}' must be a number.")
    if timeout <= 0:
        raise ArgumentTypeError(f"Timeout '{value}' must be greater than 0.0s.")
    return timeout


def main():
    # Colorama module's initialization.
    init(autoreset=True)

    version_string = f"%(prog)s {__version__}\n" +  \
                     f"{requests.__description__}:  {requests.__version__}\n" + \
                     f"Python:  {platform.python_version()}"

    parser = ArgumentParser(formatter_class=RawDescriptionHelpFormatter,
                            description=f"{module_name} (Version {__version__})"
                            )
    parser.add_argument("--version",
                        action="version",  version=version_string,
                        help="Display version information and dependencies."
                        )
    parser.add_argument("--verbose", "-v", "-d", "--debug",
                        action="store_true",  dest="verbose", default=False,
                        help="Display extra debugging information and metrics."
                        )
    parser.add_argument("--rank", "-r",
                        action="store_true", dest="rank", default=False,
                        help="Present websites ordered by their Alexa.com global rank in popularity.")
    parser.add_argument("--folderoutput", "-fo", dest="folderoutput",
                        help="If using multiple usernames, the output of the results will be saved to this folder."
                        )
    parser.add_argument("--output", "-o", dest="output",
                        help="If using single username, the output of the result will be saved to this file."
                        )
    parser.add_argument("--tor", "-t",
                        action="store_true", dest="tor", default=False,
                        help="Make requests over Tor; increases runtime; requires Tor to be installed and in system path.")
    parser.add_argument("--unique-tor", "-u",
                        action="store_true", dest="unique_tor", default=False,
                        help="Make requests over Tor with new Tor circuit after each request; increases runtime; requires Tor to be installed and in system path.")
    parser.add_argument("--csv",
                        action="store_true",  dest="csv", default=False,
                        help="Create Comma-Separated Values (CSV) File."
                        )
    parser.add_argument("--site",
                        action="append", metavar='SITE_NAME',
                        dest="site_list", default=None,
                        help="Limit analysis to just the listed sites. Add multiple options to specify more than one site."
                        )
    parser.add_argument("--proxy", "-p", metavar='PROXY_URL',
                        action="store", dest="proxy", default=None,
                        help="Make requests over a proxy. e.g. socks5://127.0.0.1:1080"
                        )
    parser.add_argument("--json", "-j", metavar="JSON_FILE",
                        dest="json_file", default=None,
                        help="Load data from a JSON file or an online, valid, JSON file.")
    parser.add_argument("--timeout",
                        action="store", metavar='TIMEOUT',
                        dest="timeout", type=timeout_check, default=None,
                        help="Time (in seconds) to wait for response to requests. "
                             "Default timeout of 60.0s."
                             "A longer timeout will be more likely to get results from slow sites."
                             "On the other hand, this may cause a long delay to gather all results."
                        )
    parser.add_argument("--print-found",
                        action="store_true", dest="print_found_only", default=False,
                        help="Do not output sites where the username was not found."
                        )
    parser.add_argument("--no-color",
                        action="store_true", dest="no_color", default=False,
                        help="Don't color terminal output"
                        )
    parser.add_argument("username",
                        nargs='+', metavar='USERNAMES',
                        action="store",
                        help="One or more usernames to check with social networks."
                        )

    args = parser.parse_args()


    # Argument check
    # TODO regex check on args.proxy
    if args.tor and (args.proxy != None):
        raise Exception("Tor and Proxy cannot be set at the same time.")

    # Make prompts
    if args.proxy != None:
        print("Using the proxy: " + args.proxy)

    if args.tor or args.unique_tor:
        print("Using Tor to make requests")
        print("Warning: some websites might refuse connecting over Tor, so note that using this option might increase connection errors.")

    # Check if both output methods are entered as input.
    if args.output is not None and args.folderoutput is not None:
        print("You can only use one of the output methods.")
        sys.exit(1)

    # Check validity for single username output.
    if args.output is not None and len(args.username) != 1:
        print("You can only use --output with a single username")
        sys.exit(1)


    #Create object with all information about sites we are aware of.
    try:
        sites = SitesInformation(args.json_file)
    except Exception as error:
        print(f"ERROR:  {error}")
        sys.exit(1)

    #Create original dictionary from SitesInformation() object.
    #Eventually, the rest of the code will be updated to use the new object
    #directly, but this will glue the two pieces together.
    site_data_all = {}
    for site in sites:
        site_data_all[site.name] = site.information

    if args.site_list is None:
        # Not desired to look at a sub-set of sites
        site_data = site_data_all
    else:
        # User desires to selectively run queries on a sub-set of the site list.

        # Make sure that the sites are supported & build up pruned site database.
        site_data = {}
        site_missing = []
        for site in args.site_list:
            for existing_site in site_data_all:
                if site.lower() == existing_site.lower():
                    site_data[existing_site] = site_data_all[existing_site]
            if not site_data:
                # Build up list of sites not supported for future error message.
                site_missing.append(f"'{site}'")

        if site_missing:
            print(
                f"Error: Desired sites not found: {', '.join(site_missing)}.")
            sys.exit(1)

    if args.rank:
        # Sort data by rank
        site_dataCpy = dict(site_data)
        ranked_sites = sorted(site_data, key=lambda k: ("rank" not in k, site_data[k].get("rank", sys.maxsize)))
        site_data = {}
        for site in ranked_sites:
            site_data[site] = site_dataCpy.get(site)

    # Run report on all specified users.
    for username in args.username:
        print()

        results = sherlock(username,
                           site_data,
                           verbose=args.verbose,
                           tor=args.tor,
                           unique_tor=args.unique_tor,
                           proxy=args.proxy,
                           print_found_only=args.print_found_only,
                           timeout=args.timeout,
                           color=not args.no_color)

        if args.output:
            result_file = args.output
        elif args.folderoutput:
            # The usernames results should be stored in a targeted folder.
            # If the folder doesn't exist, create it first
            os.makedirs(args.folderoutput, exist_ok=True)
            result_file = os.path.join(args.folderoutput, f"{username}.txt")
        else:
            result_file = f"{username}.txt"

        with open(result_file, "w", encoding="utf-8") as file:
            exists_counter = 0
            for website_name in results:
                dictionary = results[website_name]
                if dictionary.get("status").status == QueryStatus.CLAIMED:
                    exists_counter += 1
                    file.write(dictionary["url_user"] + "\n")
            file.write(f"Total Websites Username Detected On : {exists_counter}")

        if args.csv == True:
            with open(username + ".csv", "w", newline='', encoding="utf-8") as csv_report:
                writer = csv.writer(csv_report)
                writer.writerow(['username',
                                 'name',
                                 'url_main',
                                 'url_user',
                                 'exists',
                                 'http_status',
                                 'response_time_s'
                                 ]
                                )
                for site in results:
                    writer.writerow([username,
                                     site,
                                     results[site]['url_main'],
                                     results[site]['url_user'],
                                     str(results[site]['status'].status),
                                     results[site]['http_status'],
                                     results[site]['response_time_s']
                                     ]
                                    )


if __name__ == "__main__":
    main()
