import numpy as np
import pandas as pd
import json
import joblib
from pmm_tools_function import join_vhms_with_pap, make_smooth, from_pandas_to_json, estimate_rul
from datetime import datetime, timedelta

def add_response_identity(response):
    response["__dt"] = datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M:%S")
    response["__ts"] = int((datetime.now() - datetime(1970,1,1)).total_seconds())
    # add other required fields if necessary
    # example data["requester"] = "CBM" or data["requester"] = "Customer Portal"
    return response

def validate_data(data):
    # function to validate data before converting to pandas DF
    if type(data)==dict:
        return [data]
    elif type(data)==list:
        return data
    else:
        return None

def add_rul_prediction(response, required_trend_hour=4000):
    # get number of records of trend_length_hour by dividing it with 20 hours per records
    required_trend_record = int(required_trend_hour/20)

    # get field health_score_data from calculate_health_score response
    hs_data = response.get("health_score_data")
    # convert dict response to pandas dataframe
    hs_df = pd.DataFrame(hs_data)
    # ensure smr and health_score data types
    hs_df["smr"] = hs_df["smr"].astype(float)
    hs_df["health_score"] = hs_df["health_score"].astype(float)

    # for each serial_number, predict RUL
    rul_prediction = []
    srl_num_list = hs_df["serial_number"].drop_duplicates().tolist()
    for srl_num in srl_num_list:

        # select serial number and reset the index to ease locate and slicing
        df = hs_df[hs_df["serial_number"]==srl_num].reset_index()

        # check if length of healh-score trend met the minimum length requirements
        if len(df) >= required_trend_record:
            # get latest required record only
            df_latest = df.sort_values("smr", ascending=False).loc[:required_trend_record]
            # sort back to smr ascending
            df_latest = df_latest.sort_values("smr", ascending=True)
            smr = df_latest["smr"]
            hs = df_latest["health_score"]
            rul_insight = estimate_rul(smr=smr, hs=hs)
            rul_insight["serial_number"] = srl_num
            rul_prediction.append(rul_insight)
    
    # conert rul_prediction result to pandas dataframe before converting it to json
    rul_prediction_df = pd.DataFrame(rul_prediction)
    rul_rediction_json = from_pandas_to_json(rul_prediction_df)

    # add rul_prediction to response
    response["rul_prediction"] = rul_rediction_json

    return response

def calculate_health_score(data):
    # ensure data in json
    if type(data)==str:
        data = json.loads(data)
    unit_model = data.get("unit_model")
    component = data.get("component")

    # validate data vhms
    vhms = data.get("vhms")
    vhms = pd.DataFrame(validate_data(vhms))
    vhms['UNIT_MODL'] = unit_model.upper()

    # validate data pap
    pap = data.get("pap")
    pap = validate_data(pap)
    if pap is not None and len(pap)>0:
        pap = pd.DataFrame(validate_data(pap))
    else:
        pap = None

    # load trained data-preparation pipeline and machine learning model
    model_id = unit_model.lower() + "_" + component.lower()
    vhms_pipe = joblib.load('model/{}_vhms_prep_pipe.pkl'.format(model_id))
    pap_pipe = joblib.load('model/{}_pap_prep_pipe.pkl'.format(model_id))
    hs_scoring_pipe = joblib.load('model/{}_health_scoring_pipe.pkl'.format(model_id))
    
    # prepare vhms and pap data before scoring
    vhms_transform = vhms_pipe.transform(vhms)
    if pap is not None:
        pap_transform = pap_pipe.transform(pap)
        scoring_dataset = join_vhms_with_pap(vhms_transform, pap_transform, time_window=30)
        scoring_dataset['with_pap'] = scoring_dataset['LAB_NUM'].map(
            lambda x: True if x is not None and x==x else False)
    else:
        scoring_dataset = vhms_transform.copy()
        scoring_dataset['with_pap'] = False
        scoring_dataset['LAB_NUM'] = None
    
    # compute health score
    hs_result = hs_scoring_pipe.transform(scoring_dataset)
    hs = hs_result[:,0].astype(np.double)
    hs = make_smooth(hs,window_size=7)
    
    # store and return the result
    result_dataset = pd.DataFrame({
        "serial_number": scoring_dataset['UNIT_SRL_NUM'],
        "smr": scoring_dataset["SMR"],
        "timestamp": scoring_dataset['TIMESTAMP'],
        "health_score": hs,
        "pap_ref_lab_num": scoring_dataset['LAB_NUM']
    })    
    health_score_result = from_pandas_to_json(result_dataset)
    output_data = {
        # data header. add underscore ("_") so that it appears above alphabethically
        "_unit_model": unit_model.upper(),
        "_component": component,
        # data content
        "health_score_data": health_score_result
    }
    return output_data
